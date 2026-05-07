import time
import datetime
import json
import random
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
from smart_logic import get_clock, DelayCalculator

_calculator = DelayCalculator()

@AgentServer.custom_action("SmartClick")
class SmartClickAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        param = {}
        try:
            if argv.custom_action_param:
                if isinstance(argv.custom_action_param, str):
                    param = json.loads(argv.custom_action_param)
                elif isinstance(argv.custom_action_param, dict):
                    param = argv.custom_action_param
        except Exception as e:
            print(f"Error parsing custom_action_param: {e}")
            
        # Parse flat config into DelayCalculator format
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
        
        # 1. Target Time Wait
        target_time_str = param.get('target_time', '')
        if target_time_str:
            try:
                clock = get_clock()
                now_time = clock.now()
                # Parse "12:00:00.000" and combine with today's date
                time_parts = target_time_str.replace(',', '.').split('.')
                hms = time_parts[0]
                ms = int(time_parts[1]) if len(time_parts) > 1 else 0
                parsed_time = datetime.datetime.strptime(hms, "%H:%M:%S")
                
                target_dt = now_time.replace(
                    hour=parsed_time.hour,
                    minute=parsed_time.minute,
                    second=parsed_time.second,
                    microsecond=ms * 1000
                )
                
                # if target is in the past, maybe it's for tomorrow? 
                if (now_time - target_dt).total_seconds() > 3600:
                    target_dt += datetime.timedelta(days=1)
                    
                wait_sec = (target_dt - now_time).total_seconds()
                if wait_sec > 0:
                    print(f"SmartClick waiting {wait_sec:.3f}s for target time {target_dt}")
                    if wait_sec > 0.05:
                        time.sleep(wait_sec - 0.02)
                    while clock.now() < target_dt:
                        pass # spin lock for last precision
            except Exception as e:
                print(f"Error waiting for target time: {e}")
                
        # 2. Calculate delay
        delay_ms, range_name = _calculator.get_click_delay(calc_config)
        print(f"SmartClick executing delay: {delay_ms}ms (Range: {range_name})")
        
        # 3. Apply delay
        time.sleep(delay_ms / 1000.0)
        
        # 4. Perform click
        box = argv.box
        if box and box.width > 0 and box.height > 0:
            cx = box.x + random.randint(0, box.width - 1)
            cy = box.y + random.randint(0, box.height - 1)
        elif box:
            cx, cy = box.x, box.y
        else:
            cx, cy = 0, 0 
            
        print(f"SmartClick posting click to ({cx}, {cy})")
        if context and context.tasker and context.tasker.controller:
            context.tasker.controller.post_click(cx, cy).wait()
        
        return True
