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
        confirm_center = json.loads(param.get('confirm_target', '[1088, 622]'))
        second_confirm_center = json.loads(param.get('second_confirm_target', '[815, 467]'))
        exit_center = json.loads(param.get('exit_target', '[649, 630]'))
        refresh_center = json.loads(param.get('refresh_target', '[917, 117]'))

        # --- jump_time 开关 ---
        jump_enabled = str(param.get('jump_time_enabled', '0')) == '1'

        # --- 获取时钟和延迟 ---
        clock = get_clock()
        user_delay_ms, chosen_range = _calculator.get_click_delay(calc_config)
        user_delay_sec = user_delay_ms / 1000.0
        print(f"[SmartClick] 选择模式: {chosen_range}, 延迟: {user_delay_ms}ms")

        # --- 计算目标时间戳 ---
        # 优先使用 OCR 推算的 target_base_ts，其次使用用户手动输入
        target_ts = None
        sale_ts = None

        with _ocr_lock:
            _ocr_base = ocr_target_base_ts
            _ocr_sale = ocr_sale_ts
            if ocr_target_base_ts is not None:
                ocr_sale_ts = None
                ocr_target_base_ts = None

        if _ocr_base is not None:
            # OCR 模式：target = 0秒时刻 + 用户延迟
            sale_ts = _ocr_sale
            target_ts = _ocr_base + user_delay_sec
            print(f"[SmartClick] 使用 OCR 推算: sale_ts={sale_ts}, target_ts={target_ts}")
        else:
            # 手动模式：用户输入 target_time
            target_time_str = param.get('target_time', '')
            if target_time_str:
                try:
                    now_time = clock.now()
                    time_parts = target_time_str.replace(',', '.').split('.')
                    hms = time_parts[0]
                    ms = int(time_parts[1]) if len(time_parts) > 1 else 0
                    parsed_time = datetime.datetime.strptime(hms, "%H:%M:%S")
                    target_dt = now_time.replace(
                        hour=parsed_time.hour, minute=parsed_time.minute,
                        second=parsed_time.second, microsecond=ms * 1000
                    )
                    if (now_time - target_dt).total_seconds() > 3600:
                        target_dt += datetime.timedelta(days=1)
                    sale_ts = clock.get_real_timestamp()
                    delta_sec = (target_dt - now_time).total_seconds()
                    target_ts = sale_ts + delta_sec + user_delay_sec
                    print(f"[SmartClick] 使用手动输入: target_dt={target_dt}, delay={user_delay_ms}ms")
                except Exception as e:
                    print(f"[SmartClick] 解析手动 target_time 失败: {e}")

        if target_ts is None:
            # 无目标时间，直接延迟后点击
            print(f"[SmartClick] 无目标时间，直接延迟 {user_delay_ms}ms 后点击")
            _precise_sleep(user_delay_sec)
            box = argv.box
            if box and box.width > 0:
                cx = box.x + random.randint(0, box.width - 1)
                cy = box.y + random.randint(0, box.height - 1)
            else:
                cx, cy = second_confirm_center
            context.tasker.controller.post_click(cx, cy).wait()
            return True

        # ========== 还原原版完整抢购流程 ==========

        # Step 1: 点击"确认区域"（预操作）
        print(f"[SmartClick] Step1: 点击确认区域 {confirm_center}")
        context.tasker.controller.post_click(confirm_center[0], confirm_center[1]).wait()

        # Step 2: 日志
        print(f"[SmartClick] 目标真实时刻: {datetime.datetime.fromtimestamp(target_ts)}")
        print("[SmartClick] Step3: 忙等到目标时刻...")

        # Step 3: perf_counter spin-lock 精确等待
        # 将 target_ts 转换为 perf_counter 域
        real_ts_now = clock.get_real_timestamp()
        perf_now = time.perf_counter()
        target_perf = perf_now + (target_ts - real_ts_now)

        jump_triggered = False
        while True:
            now_perf = time.perf_counter()
            if now_perf >= target_perf:
                break
            remaining = target_perf - now_perf
            # 原版：提前 50ms 触发 jump_time(1000)
            if jump_enabled and remaining <= 0.05 and not jump_triggered:
                threading.Thread(target=jump_time, args=(1000,), daemon=True).start()
                jump_triggered = True
                print("[SmartClick] 异步跳跃已启动 (提前50ms)")
            if remaining > 0.01:
                time.sleep(0.001)
            # < 10ms 时忙等待（空转）

        # Step 4: 精确时刻到达 → 点击"二次确认区域"
        print(f"[SmartClick] Step4: 精确时刻到达，点击二次确认区域 {second_confirm_center}")
        context.tasker.controller.post_click(
            second_confirm_center[0], second_confirm_center[1]
        ).wait()

        # 记录误差日志
        actual_ts = clock.get_real_timestamp()
        error_ms = (actual_ts - target_ts) * 1000.0
        if sale_ts:
            total_ms = (actual_ts - sale_ts) * 1000.0
        else:
            total_ms = 0
        print(f"[SmartClick] 点击完成: 模式={chosen_range}, "
              f"设置延迟={user_delay_ms}ms, 物理误差={error_ms:.2f}ms, "
              f"总耗时={total_ms:.2f}ms")

        # Step 5: 点击"退出区域"
        time.sleep(0.1)
        print(f"[SmartClick] Step5: 点击退出区域 {exit_center}")
        context.tasker.controller.post_click(exit_center[0], exit_center[1]).wait()

        # Step 6: 等待后点击"刷新区域"，NTP 重同步
        time.sleep(3.0)
        print(f"[SmartClick] Step6: 点击刷新区域 {refresh_center}")
        context.tasker.controller.post_click(refresh_center[0], refresh_center[1]).wait()

        # NTP 重同步
        try:
            clock.sync_with_ntp()
            print("[SmartClick] NTP 重同步完成")
        except Exception as e:
            print(f"[SmartClick] NTP 重同步失败: {e}")

        return True
