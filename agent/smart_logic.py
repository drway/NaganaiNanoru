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
    'time4.aliyun.com'
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
        server_weight = 1
        server = final_servers[i]
        if 'aliyun' in server or '203.107.6.88' in server:
            server_weight = 1.3
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
                    
        if samples_offsets:
            avg_offset, _, _, _ = robust_weighted_average(samples_offsets, samples_rtts, samples_servers)
            if avg_offset is not None:
                with self._lock:
                    self._offset = avg_offset
                    self._synced = True
                return True
        return False

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
        _clock_instance.sync_with_ntp()
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
