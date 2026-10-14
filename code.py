# =====================================================================
#  DISCO HOUSE  -  Adafruit Feather RP2040 Prop-Maker  (CircuitPython 10)
# =====================================================================
#  GATE OPEN  (ultrasonic distance ABOVE 15 cm):
#     NeoPixel ring = disco lights, servo = swings, speaker = your song
#  GATE CLOSED (distance 15 cm or less):
#     song stops, servo goes to the middle, ring = soft orange glow
#  LIGHT SENSOR (photocell), works all the time:
#     room BRIGHT -> RGBW strip full white
#     room DARK   -> RGBW strip mixed colours moving
#
#  WIRING
#     Ultrasonic  TRIG -> D10, ECHO -> 1k/2k divider -> D11, VCC -> BAT, GND -> GND
#     Servo       Prop-Maker 3-pin SERVO header
#     Ring        Data In -> 330 ohm -> D9, PWR -> BAT, GND -> GND
#     RGBW strip  Data In -> 330 ohm -> D6, 5V -> BAT, GND -> GND
#     Speaker     Prop-Maker green screw terminal (built-in amplifier)
#     Photocell   one leg -> A1, other leg -> GND   (no resistor)
#
#  FILES ON CIRCUITPY
#     lib/: adafruit_hcsr04.mpy, adafruit_motor/, neopixel.mpy, adafruit_pixelbuf.mpy
#     your song: any .mp3 or .wav in the top folder (e.g. SIYN.mp3)
# =====================================================================

import os
import time
import math
import random
import board
import digitalio
import pwmio
import neopixel
import audiocore
import audiobusio
import adafruit_hcsr04
from adafruit_motor import servo
from rainbowio import colorwheel

# ============================ SETTINGS ===============================
OPEN_ABOVE_CM = 15.0        # distance above this = gate OPEN = party
CONFIRM_READINGS = 5        # steady readings in a row before switching
NO_ECHO_MEANS_OPEN = False  # False = ignore "no echo" readings (safer)

RING_PIXELS = 12            # LEDs on the ring
RING_BRIGHTNESS = 1.0         # 1.0 = maximum brightness
CLOSED_GLOW = (150, 70, 0)  # ring colour when the gate is closed (0,0,0 = off)

SERVO_REST = 90             # middle position
SERVO_SWING = 80            # swings 90 +/- 80 -> 10..170 degrees
SERVO_SPEED = 2.5           # higher = faster swing

STRIP_PIXELS = 8            # LEDs on the RGBW strip
BRIGHT_COLOUR = (0, 0, 0, 255)  # strip in a BRIGHT room (red, green, blue, white)
COLOUR_BRIGHTNESS = 255     # strip colours in a DARK room (0-255)
COLOUR_SPEED = 150          # how fast the colours move
FLIP_PHOTOCELL = False      # set True if bright/dark come out backwards

VOLUME = 100                # song volume in % (0-100). 100 = loudest possible
# =====================================================================


print("\n========== DISCO HOUSE ==========")

# ---------- Power for the Prop-Maker servo header + speaker amplifier ----------
power = digitalio.DigitalInOut(board.EXTERNAL_POWER)
power.direction = digitalio.Direction.OUTPUT
power.value = True
time.sleep(0.3)


def start_part(name, maker):
    """Set up one part. If it fails, say so and carry on without it."""
    try:
        part = maker()
        print("OK      :", name)
        return part
    except Exception as e:
        print("PROBLEM :", name, "-", e)
        return None


sonar = start_part("ultrasonic (D10/D11)",
                   lambda: adafruit_hcsr04.HCSR04(trigger_pin=board.D10,
                                                  echo_pin=board.D11, timeout=0.05))
ring = start_part("ring (D9)",
                  lambda: neopixel.NeoPixel(board.D9, RING_PIXELS,
                                            brightness=RING_BRIGHTNESS, auto_write=False))
strip = start_part("RGBW strip (D6)",
                   lambda: neopixel.NeoPixel(board.D6, STRIP_PIXELS, brightness=1.0,
                                             auto_write=False, pixel_order=neopixel.GRBW))
disco_servo = start_part("servo (servo header)",
                         lambda: servo.Servo(pwmio.PWMOut(board.EXTERNAL_SERVO,
                                                          duty_cycle=2 ** 15, frequency=50)))
audio = start_part("speaker (green screw terminal)",
                   lambda: audiobusio.I2SOut(bit_clock=board.I2S_BIT_CLOCK,
                                             word_select=board.I2S_WORD_SELECT,
                                             data=board.I2S_DATA))


def make_photocell():
    pin = digitalio.DigitalInOut(board.A1)
    pin.direction = digitalio.Direction.INPUT
    pin.pull = digitalio.Pull.UP
    return pin


photocell = start_part("photocell (A1)", make_photocell)


# ---------- Song ----------
def find_song():
    for name in sorted(os.listdir("/")):
        if name.startswith(".") or name.startswith("_"):
            continue
        low = name.lower()
        if low.endswith(".mp3") or low.endswith(".wav"):
            return name
    return None


SONG = find_song()
print("Song    :", SONG if SONG else "none found (add a .mp3 or .wav)")
song_voice = None
song_sound = None
song_mixer = None   # kept here so it is never cleaned out of memory
song_handle = None


def setup_song():
    """Open the song and mixer once at start-up. The mixer runs all the time,
    and the song is started/stopped on its voice."""
    global song_voice, song_sound, song_mixer, song_handle
    if not audio or not SONG:
        return
    try:
        song_handle = open(SONG, "rb")
        if SONG.lower().endswith(".mp3"):
            import audiomp3
            song_sound = audiomp3.MP3Decoder(song_handle)
        else:
            song_sound = audiocore.WaveFile(song_handle)
        import audiomixer
        song_mixer = audiomixer.Mixer(voice_count=1,
                                 sample_rate=song_sound.sample_rate,
                                 channel_count=song_sound.channel_count,
                                 bits_per_sample=16, samples_signed=True,
                                 buffer_size=4096)
        audio.play(song_mixer)
        song_voice = song_mixer.voice[0]
        song_voice.level = max(0, min(VOLUME, 100)) / 100
        print("OK      : song ready,", VOLUME, "% volume")
    except Exception as e:
        print("PROBLEM : could not load", SONG, "-", e)


def play_song():
    if song_voice:
        song_voice.play(song_sound, loop=True)   # always starts from the beginning
        print("Playing :", SONG)


def stop_song():
    if song_voice:
        song_voice.stop()


setup_song()


# ---------- Ultrasonic ----------
# One ping every PING_GAP seconds. The HC-SR04 needs ~60 ms between pings,
# otherwise echoes from the last ping (bouncing around the tent) confuse it.
PING_GAP = 0.07
good_readings = []      # last few good distances
last_good_time = -10    # when we last got a good reading
pings = 0               # debug counters
echoes = 0


def ping():
    """Send ONE ping. Returns the distance in cm, or None for no echo."""
    global pings, echoes
    if not sonar:
        return None
    pings += 1
    try:
        d = sonar.distance
    except RuntimeError:
        return None
    if d <= 0 or d > 400:          # impossible values = bad reading
        return None
    echoes += 1
    return d


def get_distance(now):
    """Middle of the last 5 good readings. None if no good reading for 0.5 s."""
    global good_readings, last_good_time
    d = ping()
    if d is not None:
        good_readings.append(d)
        good_readings = good_readings[-5:]
        last_good_time = now
    if now - last_good_time > 0.5:
        good_readings = []
        return None
    if not good_readings:
        return None
    ordered = sorted(good_readings)
    return ordered[len(ordered) // 2]


# ---------- Ring ----------
def ring_disco(t):
    if not ring:
        return
    mode = int(t / 3) % 3
    if mode == 0:                                  # rainbow spin
        for i in range(RING_PIXELS):
            ring[i] = colorwheel((i * 256 // RING_PIXELS + int(t * 200)) & 255)
    elif mode == 1:                                # random sparkles
        ring.fill(0)
        for _ in range(3):
            ring[random.randrange(RING_PIXELS)] = colorwheel(random.randrange(256))
    else:                                          # chasing dots
        ring.fill(0)
        pos = int(t * 12)
        for k in range(0, RING_PIXELS, 4):
            ring[(pos + k) % RING_PIXELS] = colorwheel((pos * 10) & 255)
    ring.show()


def ring_closed():
    if ring:
        ring.fill(CLOSED_GLOW)
        ring.show()


# ---------- Servo ----------
def servo_rest():
    if disco_servo:
        disco_servo.angle = SERVO_REST


def servo_swing(t):
    if disco_servo:
        disco_servo.angle = SERVO_REST + SERVO_SWING * math.sin(t * SERVO_SPEED)


# ---------- Light sensor + RGBW strip ----------
def is_bright():
    """Bright light lets the photocell conduct, pulling A1 LOW."""
    if not photocell:
        return True
    bright = not photocell.value
    return (not bright) if FLIP_PHOTOCELL else bright


def update_strip(bright, t):
    if not strip:
        return
    if bright:
        strip.fill(BRIGHT_COLOUR)
    else:
        for i in range(STRIP_PIXELS):
            c = colorwheel((i * 256 // STRIP_PIXELS + int(t * COLOUR_SPEED)) & 255)
            r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255
            strip[i] = (r * COLOUR_BRIGHTNESS // 255,
                        g * COLOUR_BRIGHTNESS // 255,
                        b * COLOUR_BRIGHTNESS // 255, 0)
    strip.show()


# ============================ START ==================================
servo_rest()
ring_closed()
print("Party when distance is above", OPEN_ABOVE_CM, "cm")
print("================================\n")

party = False
count = 0
party_start = 0
last_sensor = 0
last_print = 0
distance = None
bright = is_bright()

# ============================ MAIN LOOP ==============================
while True:
    now = time.monotonic()

    # --- one ultrasonic ping every PING_GAP seconds ---
    if now - last_sensor >= PING_GAP:
        last_sensor = now
        distance = get_distance(now)

        if distance is None:
            looks_open = party if not NO_ECHO_MEANS_OPEN else True  # ignore missing echoes
        else:
            looks_open = distance > OPEN_ABOVE_CM

        if looks_open != party:
            count += 1
            if count >= CONFIRM_READINGS:
                party = looks_open
                count = 0
                if party:
                    print(">>> GATE OPEN - PARTY ON")
                    party_start = now
                    play_song()
                else:
                    print(">>> GATE CLOSED - PARTY OFF")
                    stop_song()
                    servo_rest()
                    ring_closed()
        else:
            count = 0

    # --- light sensor ---
    new_bright = is_bright()
    if new_bright != bright:
        bright = new_bright
        print(">>> ROOM", "BRIGHT - strip white" if bright else "DARK - strip colours")
    update_strip(bright, now)

    # --- party animation ---
    if party:
        t = now - party_start
        ring_disco(t)
        servo_swing(t)

    # --- status twice a second ---
    if now - last_print >= 0.5:
        last_print = now
        dist_text = "no echo" if distance is None else "%.1f cm" % distance
        print("Distance:", dist_text,
              "| Gate:", "OPEN - PARTY!" if party else "closed",
              "| Room:", "BRIGHT" if bright else "DARK",
              "| Echoes:", str(echoes) + "/" + str(pings))
        pings = 0
        echoes = 0

    time.sleep(0.01)
