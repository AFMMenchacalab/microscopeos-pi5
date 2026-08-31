import RPi.GPIO as GPIO
import time

DIR = 20
STEP = 21

GPIO.setmode(GPIO.BCM)
GPIO.setup(DIR, GPIO.OUT)
GPIO.setup(STEP, GPIO.OUT)

GPIO.output(DIR, GPIO.HIGH)

delay = 0.005  # más estable

try:
    for i in range(800):
        GPIO.output(STEP, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        time.sleep(delay)

    time.sleep(1)

    GPIO.output(DIR, GPIO.LOW)

    for i in range(800):
        GPIO.output(STEP, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        time.sleep(delay)

finally:
    GPIO.cleanup()
