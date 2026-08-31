from gpiozero import LED
from time import sleep
import subprocess
from datetime import datetime

led = LED(17)

try:
	print ("Led is on")
	led.on()

	sleep(0.3)

	#Fliename date&time
	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
	filename = f"foto_{timestamp}.png"

	print ("Taking picture")
	subprocess.run(["rpicam-still", "-o", filename])

	print ("Led is off")
	led.off()

except KeyboardInterrupt:
	led.off()
	print ("led is off manually")
