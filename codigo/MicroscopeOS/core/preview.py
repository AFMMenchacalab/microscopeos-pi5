import threading
import cv2
import time


class PreviewManager:

    def __init__(self, camera):
        self.camera = camera
        self.running = False
        self.thread = None

    def _run(self):

        print("Preview iniciado.")
        cv2.namedWindow("Microscope Preview", cv2.WINDOW_NORMAL)

        while self.running:

            frame = self.camera.get_frame()

            cv2.imshow("Microscope Preview", frame)

            # Espera 1 ms y permite detectar tecla
            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.stop()
                break

        cv2.destroyAllWindows()
        print("Preview detenido.")

    def start(self):
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join()
