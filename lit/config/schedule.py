import os

COURSE_TIME_MAP = {
    1: "08:00", 2: "08:55", 3: "10:00", 4: "10:55",
    5: "14:30", 6: "15:25", 7: "16:30", 8: "17:25",
    9: "19:10", 10: "20:05",
}

REMIND_BEFORE_MINUTES = int(os.getenv("REMIND_BEFORE_MINUTES", "30"))

COOKIE_REFRESH_INTERVAL = int(os.getenv("COOKIE_REFRESH_INTERVAL", "3600"))