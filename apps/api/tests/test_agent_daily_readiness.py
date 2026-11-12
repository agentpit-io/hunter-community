import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from app.services.quant import screen_asof, scheduler


class AgentDailyReadinessTests(unittest.TestCase):
    def test_empty_store_is_not_cached_after_initialization(self):
        screen_asof._cache.clear()
        empty = {'codes': {}, 'bench': {}, 'last': None}
        ready = {'codes': {'PLTR': []}, 'bench': {}, 'last': '2026-10-08'}
        with patch.object(screen_asof, '_load_store', side_effect=[empty, ready]) as load:
            self.assertIs(screen_asof.get_store('us', {}), empty)
            self.assertIs(screen_asof.get_store('us', {}), ready)
            self.assertIs(screen_asof.get_store('us', {}), ready)
            self.assertEqual(load.call_count, 2)
        screen_asof._cache.clear()

    def test_scheduler_uses_shanghai_even_on_utc_host(self):
        class Recorder:
            def __init__(self): self.jobs = {}
            def add_job(self, function, trigger, **kwargs): self.jobs[kwargs['id']] = trigger
        recorder = Recorder()
        with patch.dict('os.environ', {'QUANT_RS_HISTORY_DISABLED': '0'}):
            scheduler.register(recorder)
        for trigger in recorder.jobs.values():
            self.assertEqual(str(trigger.timezone), 'Asia/Shanghai')
        trigger = recorder.jobs['quant_rs_history_us']
        next_time = trigger.get_next_fire_time(None, datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(next_time.astimezone(timezone.utc), datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc))


if __name__ == '__main__':
    unittest.main()
