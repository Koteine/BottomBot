import cv2
import numpy as np
import mss
import pyautogui
import customtkinter as ctk
import threading
import time

class MiniBot:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("AutoDance")
        self.window.geometry("300x200")
        self.window.attributes("-topmost", True)
        
        self.is_active = False
        
        self.btn = ctk.CTkButton(self.window, text="СТАРТ", command=self.switch)
        self.btn.pack(pady=40)
        
        self.label = ctk.CTkLabel(self.window, text="Статус: Выключен")
        self.label.pack()

    def switch(self):
        if not self.is_active:
            self.is_active = True
            self.btn.configure(text="СТОП", fg_color="red")
            self.label.configure(text="Статус: РАБОТАЕТ")
            threading.Thread(target=self.run_logic, daemon=True).start()
        else:
            self.is_active = False
            self.btn.configure(text="СТАРТ", fg_color=["#3a7ebf", "#1f538d"])
            self.label.configure(text="Статус: Выключен")

    def run_logic(self):
        # Координаты 1600x900
        zone = {"top": 720, "left": 400, "width": 800, "height": 120}
        template = cv2.imread('assets/down.png', cv2.IMREAD_GRAYSCALE)
        
        with mss.mss() as sct:
            while self.is_active:
                img = np.array(sct.grab(zone))
                gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
                
                if template is not None:
                    res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
                    if np.any(res > 0.8):
                        pyautogui.press('down')
                        time.sleep(0.05)
                time.sleep(0.1)

if __name__ == "__main__":
    bot = MiniBot()
    bot.window.mainloop()
