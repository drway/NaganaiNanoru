import re
import json
import time
import datetime
import smart_click
from smart_logic import get_clock

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from maa.context import Context


def parse_countdown(text: str):
    """
    还原原版 _handle_ocr_result 的倒计时解析逻辑。
    只解析 "X分Y秒" 格式，分钟只取最后一位数字。
    返回 total_seconds (int) 或 None。
    """
    fen_index = text.find('分')
    miao_index = text.find('秒')
    if fen_index == -1 or miao_index == -1 or '秒后' not in text:
        return None
    try:
        minutes_str = text[:fen_index].strip()
        # 原版只取分钟字符串的最后一位数字
        minute_digit = int(minutes_str[-1]) if minutes_str else 0
        seconds_str = text[fen_index + 1:miao_index].strip()
        seconds = int(seconds_str) if seconds_str else 0
        total_seconds = minute_digit * 60 + seconds
        return total_seconds
    except (ValueError, IndexError):
        return None


@AgentServer.custom_recognition("CountdownOCR")
class CountdownOCR(CustomRecognition):
    """
    自定义识别器：使用 MaaFW 内置 OCR 识别倒计时文字，
    解析剩余秒数，在 total_seconds == 3 时返回成功并计算 target_ts。
    还原原版 ocr_thread.py + _handle_ocr_result 的核心逻辑。
    """
    _last_total_seconds = None

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        print(f"[CountdownOCR] analyze entered | roi={argv.roi} | image_type={type(argv.image).__name__}")
        try:
            return self._analyze_impl(context, argv)
        except Exception as e:
            import traceback
            print(f"[CountdownOCR] 异常: {e}")
            print(traceback.format_exc())
            return None

    def _analyze_impl(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:

        # 解析 custom_recognition_param 以获取刷新区域坐标
        reco_param = {}
        if argv.custom_recognition_param:
            try:
                reco_param = json.loads(argv.custom_recognition_param)
            except Exception:
                pass
        refresh_raw = reco_param.get('refresh_target', '[905, 107, 23, 21]')
        if isinstance(refresh_raw, str):
            refresh_target = json.loads(refresh_raw)
        else:
            refresh_target = refresh_raw

        # 使用 MaaFW 内置 OCR 识别倒计时区域
        reco_detail = context.run_recognition(
            "CountdownOCRInternal",
            argv.image,
            pipeline_override={
                "CountdownOCRInternal": {
                    "recognition": {
                        "type": "OCR",
                        "param": {
                            "roi": list(argv.roi) if argv.roi else [1033, 583, 111, 16],
                            "expected": [".*[分秒].*"]
                        }
                    }
                }
            }
        )

        if not reco_detail or not reco_detail.best_result:
            return None

        text = reco_detail.best_result.text

        total_seconds = parse_countdown(text)
        if total_seconds is None:
            return None

        now_str = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        print(f"[OCR] 解析总秒数: {total_seconds}  ({now_str})")

        # 去重：秒数没有变化时不重复处理
        if total_seconds == self._last_total_seconds:
            return None
        self._last_total_seconds = total_seconds

        # 在 16/12/7 秒时触发刷新（通过 context 点击刷新区域）
        if total_seconds in [16, 12, 7]:
            print(f"[流程] 倒计时 {total_seconds}s，触发刷新")
            try:
                _rx = refresh_target[0] + (refresh_target[2] // 2 if len(refresh_target) == 4 else 0)
                _ry = refresh_target[1] + (refresh_target[3] // 2 if len(refresh_target) == 4 else 0)
                context.tasker.controller.post_click(_rx, _ry).wait()
            except Exception as e:
                print(f"[流程] 刷新点击失败: {e}")
            return None

        # 在 2 秒时输出准备日志
        if total_seconds == 2:
            print("[状态] 倒计时2秒，准备抢购")
            return None

        # ★ 核心逻辑：倒计时 == 3 秒时锁定目标时间
        if total_seconds == 3:
            clock = get_clock()
            sale_ts = int(clock.get_real_timestamp())
            print(f"[抢购] 记录3秒整秒时刻: {datetime.datetime.fromtimestamp(sale_ts).strftime('%H:%M:%S')}")

            # 写入共享变量供 SmartClick 使用（加锁保护）
            with smart_click._ocr_lock:
                smart_click.ocr_sale_ts = sale_ts
                smart_click.ocr_target_base_ts = sale_ts + 3.0

            box = reco_detail.best_result.box
            return CustomRecognition.AnalyzeResult(
                box=box,
                detail=f"countdown_3s_sale_ts={sale_ts}"
            )

        return None
