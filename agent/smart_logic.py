import time
import datetime
import threading
import ntplib
import random
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

NTP_SERVERS = [
    '203.107.6.88',
    'time1.aliyun.com',
    'ntp2.aliyun.com',
    'ntp.aliyun.com',
    'time2.aliyun.com',
    'time4.aliyun.com',
    'ntp.tencent.com',
    'ntp1.tencent.com',
    'ntp2.tencent.com',
    'ntp3.tencent.com',
    'ntp4.tencent.com',
    'ntp5.tencent.com',
]

def robust_weighted_average(offsets, rtts, servers, outlier_threshold=2.5):
    if not offsets:
        return None, None, None, 0
    offsets_array = np.array(offsets)
    rtts_array = np.array(rtts)
    
    rtt_mask = rtts_array < 2
    if not np.any(rtt_mask):
        rtt_mask = rtts_array < 5
    offset_mask = np.abs(offsets_array) < 1
    if not np.any(offset_mask):
        offset_mask = np.abs(offsets_array) < 5
        
    valid_mask = rtt_mask & offset_mask
    valid_indices = np.where(valid_mask)[0]
    if len(valid_indices) == 0:
        valid_indices = np.arange(len(offsets))
        
    valid_offsets = offsets_array[valid_indices]
    valid_rtts = rtts_array[valid_indices]
    valid_servers = [servers[i] for i in valid_indices]
    
    if len(valid_offsets) > 3:
        median_offset = np.median(valid_offsets)
        mad = np.median(np.abs(valid_offsets - median_offset))
        if mad > 0:
            z_scores = 0.6745 * (valid_offsets - median_offset) / mad
            outlier_mask = np.abs(z_scores) < outlier_threshold
            final_indices = np.where(outlier_mask)[0]
            if len(final_indices) > 0:
                final_offsets = valid_offsets[final_indices]
                final_rtts = valid_rtts[final_indices]
                final_servers = [valid_servers[i] for i in final_indices]
            else:
                final_offsets = np.array([median_offset])
                final_rtts = np.array([np.median(valid_rtts)])
                final_servers = [valid_servers[np.argmin(np.abs(valid_offsets - median_offset))]]
        else:
            final_offsets = valid_offsets
            final_rtts = valid_rtts
            final_servers = valid_servers
    else:
        final_offsets = valid_offsets
        final_rtts = valid_rtts
        final_servers = valid_servers
        
    weights = []
    for i in range(len(final_offsets)):
        base_weight = 1 / (final_rtts[i] + 0.001)
        server_weight = 1.0
        server = final_servers[i]
        if 'aliyun' in server or 'tencent' in server:
            server_weight = 1.3
        elif '203.107.6.88' in server:
            server_weight = 1.2
        weights.append(base_weight * server_weight)
        
    weights = np.array(weights)
    if np.sum(weights) > 0:
        weights = weights / np.sum(weights)
    else:
        weights = np.ones_like(weights) / len(weights)
        
    avg_offset = np.sum(final_offsets * weights)
    avg_rtt = np.sum(final_rtts * weights)
    best_server_idx = np.argmax(weights)
    best_server = final_servers[best_server_idx]
    
    return avg_offset, avg_rtt, best_server, len(final_offsets)

class MonotonicClock:
    def __init__(self):
        self._monotonic_start = time.monotonic()
        self._real_time_start = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        self._offset = 0
        self._lock = threading.Lock()
        self._synced = False
        self._perf_base_time = None   # 用于 perf_counter 基准
        self._perf_base_perf = None

    def sync_with_ntp(self, timeout=3, max_workers=5):
        client = ntplib.NTPClient()
        samples_offsets = []
        samples_rtts = []
        samples_servers = []

        servers_to_try = random.sample(NTP_SERVERS, min(5, len(NTP_SERVERS)))

        def query(server):
            try:
                response = client.request(server, version=3, timeout=timeout)
                return response.offset, response.delay, server
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(query, s) for s in servers_to_try]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    samples_offsets.append(res[0])
                    samples_rtts.append(res[1])
                    samples_servers.append(res[2])
                    print(f"[NTP] {res[2]} 可用 - 延迟: {res[1]*1000:.2f}ms, 偏移: {res[0]*1000:.2f}ms")

        if not samples_offsets:
            print("[NTP] 所有服务器均不可用")
            return False

        avg_offset, avg_rtt, best_server, n = robust_weighted_average(
            samples_offsets, samples_rtts, samples_servers)
        if avg_offset is None:
            return False

        with self._lock:
            self._offset = avg_offset
            self._synced = True
            self._perf_base_time = time.time() + avg_offset
            self._perf_base_perf = time.perf_counter()
            synced_real = self._perf_base_time

        now_str = datetime.datetime.fromtimestamp(synced_real).strftime('%H:%M:%S.%f')[:-3]
        print(f"[NTP] 同步完成 | 参与服务器: {n}/{len(samples_offsets)} | "
              f"加权偏移: {avg_offset*1000:.2f}ms | 加权RTT: {avg_rtt*1000:.2f}ms | "
              f"最优: {best_server}")
        print(f"[基准] 当前真实时间: {now_str}")
        return True

    def _start_periodic_sync(self, interval=300):
        """启动周期性 NTP 重同步 daemon 线程"""
        def _sync_loop():
            while True:
                time.sleep(interval)
                try:
                    self.sync_with_ntp()
                except Exception as e:
                    print(f"[NTP] 周期性重同步失败: {e}")
        t = threading.Thread(target=_sync_loop, daemon=True)
        t.start()

    def get_real_timestamp(self):
        """返回基于 perf_counter 推算的真实时间戳（不受系统时间修改影响）"""
        with self._lock:
            if self._perf_base_time is not None:
                elapsed = time.perf_counter() - self._perf_base_perf
                return self._perf_base_time + elapsed
            else:
                return time.time()

    def now(self):
        with self._lock:
            elapsed = time.monotonic() - self._monotonic_start
            return self._real_time_start + datetime.timedelta(seconds=elapsed + self._offset)
            
    def timestamp(self):
        with self._lock:
            elapsed = time.monotonic() - self._monotonic_start
            return (self._real_time_start - datetime.datetime(1970, 1, 1)).total_seconds() + elapsed + self._offset

_clock_instance = None
def get_clock():
    global _clock_instance
    if _clock_instance is None:
        _clock_instance = MonotonicClock()
        print("[基准] 正在与 NTP 建立时间基准...")
        ok = _clock_instance.sync_with_ntp()
        if not ok:
            fallback = datetime.datetime.fromtimestamp(time.time()).strftime('%H:%M:%S.%f')[:-3]
            print(f"[基准] NTP 失败，使用本地时间 (误差可能增大): {fallback}")
        _clock_instance._start_periodic_sync(interval=300)
    return _clock_instance

class DelayCalculator:
    def __init__(self):
        self.last_range = None
        
    def get_click_delay(self, config):
        if str(config.get('fixed_enabled', '0')) == '1' or config.get('fixed_enabled') is True:
            return int(config.get('fixed_delay', 850)), '固定'
            
        ranges = config.get('ranges', {})
        enabled_ranges = []
        enabled_weights = []
        
        for name, cfg in ranges.items():
            if str(cfg.get('enabled', '1')) == '1' or cfg.get('enabled') is True:
                enabled_ranges.append(name)
                enabled_weights.append(int(cfg.get('weight', 1)))
                
        if not enabled_ranges:
            return 850, '默认'
            
        chosen = None
        max_attempts = 5
        for _ in range(max_attempts):
            candidate = random.choices(enabled_ranges, enabled_weights)[0]
            if candidate != '高' or self.last_range != '高':
                chosen = candidate
                break
                
        if chosen is None:
            # Fallback to non-high if possible
            non_high = [(n, w) for n, w in zip(enabled_ranges, enabled_weights) if n != '高']
            if non_high:
                chosen = random.choices([n for n, w in non_high], [w for n, w in non_high])[0]
            else:
                chosen = enabled_ranges[0]
                
        self.last_range = chosen
        cfg = ranges[chosen]
        
        # safely get min/max
        min_val = int(cfg.get('min', 800))
        max_val = int(cfg.get('max', 850))
        if min_val > max_val:
            min_val, max_val = max_val, min_val
            
        delay = random.randint(min_val, max_val)
        return delay, chosen
