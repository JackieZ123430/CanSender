import threading
import time
import can


class _LoopSender(threading.Thread):
    def __init__(self, service, arb_id, builder, cycle_s, label):
        super().__init__(daemon=True, name=f'MENU_{label}')
        self.service = service
        self.arb_id = arb_id
        self.builder = builder
        self.cycle_s = cycle_s

    def run(self):
        next_t = time.perf_counter()
        while self.service.running.is_set():
            if self.arb_id == -1:
                result = self.builder()
                if isinstance(result, tuple):
                    aid, data = result
                else:
                    aid, data = 0x000, b''
            else:
                aid = self.arb_id
                data = self.builder()
            if aid != 0x000 and data:
                try:
                    self.service.bus.send(can.Message(arbitration_id=aid, data=data, is_extended_id=False))
                except can.CanError:
                    pass
            next_t += self.cycle_s
            s = next_t - time.perf_counter()
            if s > 0:
                time.sleep(s)
            else:
                next_t = time.perf_counter()


class MenuKeepaliveService:
    def __init__(self, bus):
        self.bus = bus
        self.running = threading.Event()
        self.threads = []
        self.active = False

    def start(self, senders: list[tuple[int, callable, float, str]]):
        self.stop()
        self.running.set()
        self.threads = [_LoopSender(self, arb_id, builder, cycle_s, label) for arb_id, builder, cycle_s, label in senders]
        for t in self.threads:
            t.start()
        self.active = True

    def stop(self):
        if not self.active:
            return
        self.running.clear()
        for t in self.threads:
            t.join(timeout=0.25)
        self.threads = []
        self.active = False
