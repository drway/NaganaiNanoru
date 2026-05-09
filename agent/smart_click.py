import time
import datetime
import json
import random
import threading
import ctypes
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
from smart_logic import get_clock, DelayCalculator

_calculator = DelayCalculator()

# 模块级共享变量：由 CountdownOCR 识别器写入
ocr_sale_ts = None          # OCR 检测到3秒时的时间戳
ocr_target_base_ts = None   # sale_ts + 3.0（即0秒时刻的时间戳）
_ocr_lock = threading.Lock()


def _precise_sleep(duration: float):
    """
    高精度 sleep：先粗睡眠，再忙等待，误差可控制在 <1ms。
    还原原版 click_utils.py 的 _precise_sleep。
    """
    target = time.perf_counter() + duration
    if duration > 0.002:
        time.sleep(duration - 0.001)
    while time.perf_counter() < target:
        pass


def jump_time(ms: int):
    """
    将系统时间快进指定毫秒。还原原版 time_utils.py 的 jump_time。
    需要管理员权限。
    """
    try:
        class SYSTEMTIME(ctypes.Structure):
            _fields_ = [
                ('wYear', ctypes.c_uint16), ('wMonth', ctypes.c_uint16),
                ('wDayOfWeek', ctypes.c_uint16), ('wDay', ctypes.c_uint16),
                ('wHour', ctypes.c_uint16), ('wMinute', ctypes.c_uint16),
                ('wSecond', ctypes.c_uint16), ('wMilliseconds', ctypes.c_uint16),
            ]
        now = datetime.datetime.utcnow()
        new_time = now + datetime.timedelta(milliseconds=ms)
        st = SYSTEMTIME()
        st.wYear = new_time.year
        st.wMonth = new_time.month
        st.wDayOfWeek = new_time.isoweekday() % 7
        st.wDay = new_time.day
        st.wHour = new_time.hour
        st.wMinute = new_time.minute
        st.wSecond = new_time.second
        st.wMilliseconds = int(new_time.microsecond / 1000)
        res = ctypes.windll.kernel32.SetSystemTime(ctypes.byref(st))
        if res:
            print(f"[JumpTime] 系统时间已快进 {ms} ms")
        else:
            print("[JumpTime] 设置失败，可能没有管理员权限")
    except Exception as e:
        print(f"[JumpTime] 执行失败: {e}")


@AgentServer.custom_action("SmartClick")
class SmartClickAction(CustomAction):
    """
    还原原版 script_task.py 的完整抢购点击序列：
    1. 点击"确认区域"
    2. 预移光标到"二次确认区域"（通过 post_click 预备）
    3. perf_counter spin-lock 精确等待到 target_ts
    4. 到达时刻 → 点击"二次确认区域"
    5. 点击"退出区域"
    6. 点击"刷新区域" + NTP 重同步
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        global ocr_sale_ts, ocr_target_base_ts

        # --- 解析参数 ---
        param = {}
        try:
            if argv.custom_action_param:
                if isinstance(argv.custom_action_param, str):
                    param = json.loads(argv.custom_action_param)
                elif isinstance(argv.custom_action_param, dict):
                    param = argv.custom_action_param
        except Exception as e:
            print(f"Error parsing custom_action_param: {e}")

        # --- 解析延迟配置 ---
        calc_config = {
            'fixed_enabled': param.get('fixed_enabled', '0'),
            'fixed_delay': int(param.get('fixed_delay', '850')),
            'ranges': {
                '超低': {
                    'enabled': param.get('ultra_low_enabled', '1'),
                    'min': int(param.get('ultra_low_min', '800')),
                    'max': int(param.get('ultra_low_max', '820')),
                    'weight': int(param.get('ultra_low_weight', '1'))
                },
                '低': {
                    'enabled': param.get('low_enabled', '1'),
                    'min': int(param.get('low_min', '820')),
                    'max': int(param.get('low_max', '840')),
                    'weight': int(param.get('low_weight', '2'))
                },
                '中': {
                    'enabled': param.get('mid_enabled', '1'),
                    'min': int(param.get('mid_min', '840')),
                    'max': int(param.get('mid_max', '860')),
                    'weight': int(param.get('mid_weight', '3'))
                },
                '高': {
                    'enabled': param.get('high_enabled', '1'),
                    'min': int(param.get('high_min', '860')),
                    'max': int(param.get('high_max', '880')),
                    'weight': int(param.get('high_weight', '1'))
                }
            }
        }

        # --- 区域坐标 (720p) ---
        # 支持 [x, y] 或 [x, y, w, h]；后者在框内随机取点
        def _rand_xy(rect):
            if len(rect) == 4:
                x, y, w, h = rect
                return x + random.randint(0, max(0, w - 1)), y + random.randint(0, max(0, h - 1))
            return rect[0], rect[1]

        confirm_target = json.loads(param.get('confirm_target', '[1019, 606, 133, 28]'))
        second_confirm_target = json.loads(param.get('second_confirm_target', '[761, 451, 104, 28]'))
        exit_target = json.loads(param.get('exit_target', '[649, 630]'))
        refresh_target = json.loads(param.get('refresh_target', '[905, 107, 23, 21]'))

        # --- jump_time 开关 ---
        jump_enabled = str(param.get('jump_time_enabled', '0')) == '1'

        # --- 获取时钟和延迟 ---
        clock = get_clock()
        user_delay_ms, chosen_range = _calculator.get_click_delay(calc_config)
        user_delay_sec = user_delay_ms / 1000.0

        # --- 从 OCR 获取目标时间戳 ---
        with _ocr_lock:
            _ocr_base = ocr_target_base_ts
            _ocr_sale = ocr_sale_ts
            if ocr_target_base_ts is not None:
                ocr_sale_ts = None
                ocr_target_base_ts = None

        if _ocr_base is None:
            print("[抢购] OCR 时间戳未就绪，跳过本次")
            return False

        sale_ts = _ocr_sale
        target_ts = _ocr_base + user_delay_sec
        target_str = datetime.datetime.fromtimestamp(target_ts).strftime('%H:%M:%S.%f')[:-3]
        print(f"[抢购] 选择模式: {chosen_range}, 延迟: {user_delay_ms}ms")
        print(f"[抢购] 目标真实时刻: {target_str}")

        # ========== 还原原版完整抢购流程 ==========

        # Step 1: 点击"确认区域"
        print(f"[抢购] 点击确认区域")
        _cx, _cy = _rand_xy(confirm_target)
        context.tasker.controller.post_click(_cx, _cy).wait()

        # Step 3: perf_counter spin-lock 精确等待
        print("[等待] 忙等到目标时刻...")
        real_ts_now = clock.get_real_timestamp()
        perf_now = time.perf_counter()
        target_perf = perf_now + (target_ts - real_ts_now)

        jump_triggered = False
        while True:
            now_perf = time.perf_counter()
            if now_perf >= target_perf:
                break
            remaining = target_perf - now_perf
            if jump_enabled and remaining <= 0.05 and not jump_triggered:
                threading.Thread(target=jump_time, args=(1000,), daemon=True).start()
                jump_triggered = True
                print("[跳跃] 异步跳跃已启动 (提前50ms)")
            if remaining > 0.01:
                time.sleep(0.001)

        # Step 4: 精确时刻到达 → 点击"二次确认区域"
        _cx, _cy = _rand_xy(second_confirm_target)
        context.tasker.controller.post_click(_cx, _cy).wait()

        # 记录误差
        actual_ts = clock.get_real_timestamp()
        actual_str = datetime.datetime.fromtimestamp(actual_ts).strftime('%H:%M:%S.%f')[:-3]
        error_ms = (actual_ts - target_ts) * 1000.0
        total_ms = (actual_ts - sale_ts) * 1000.0 if sale_ts else 0
        print(f"[点击完成] 模式={chosen_range}, 设置延迟: {user_delay_ms}ms | "
              f"物理误差: {error_ms:.2f}ms | 总耗时: {total_ms:.2f}ms | 点击时刻: {actual_str}")

        # Step 5: 点击"退出区域"
        time.sleep(0.1)
        print("[流程] 点击退出区域")
        _cx, _cy = _rand_xy(exit_target)
        context.tasker.controller.post_click(_cx, _cy).wait()

        # Step 6: 等待后点击"刷新区域"，NTP 重同步
        time.sleep(3.0)
        print("[流程] 点击刷新区域，准备下一轮")
        _cx, _cy = _rand_xy(refresh_target)
        context.tasker.controller.post_click(_cx, _cy).wait()

        # NTP 重同步
        print("[NTP] 开始重同步...")
        try:
            clock.sync_with_ntp()
        except Exception as e:
            print(f"[NTP] 重同步失败: {e}")

        return True
