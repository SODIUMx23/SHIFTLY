import sqlite3
conn = sqlite3.connect('campuspulse.db')
users = [
    ("arjun","Arjun Mehta","Poster / Host","B.Tech CSE '25","VIT Boys Hostel A-Block","indigo","arjun@okaxis",250,35,0,180,4.9,"2026-10-04T09:00:00Z"),
    ("priya","Priya Sharma","Worker / Runner","BBA '26","VIT Girls Hostel C-Block","emerald","priya@ybl",45,0,67.9,310.2,5.0,"2026-10-04T09:00:00Z"),
    ("vikram","Vikram Reddy","Event Host","ECE '24","SRM Boys Hostel D-Block","amber","vikram@paytm",85,0,15,95,4.8,"2026-10-04T09:00:00Z")
]
for u in users:
    conn.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", u)
gigs = [
    ("g1","Deliver Food from Campus Cafeteria to Hostel A","Food Run",45,"High","VIT Main Cafeteria / Boys Hostel A","Pick up 2 meals from Campus Cafeteria and deliver to room 304, Hostel A.","arjun",None,"OPEN","2026-10-04T10:00:00Z"),
    ("g2","Python / DSA Tutor for Mid-Term Prep","Tutoring",350,"Medium","VIT Study Lounge (Library)","Need 1 hour help reviewing Trees, Graphs, Dijkstra before tomorrow exam.","arjun","priya","IN_PROGRESS","2026-10-04T09:30:00Z"),
    ("g3","Move Study Table & Small Fridge to Hostel D","Heavy Lifting",400,"Low","SRM Hostel D Lobby","Help move a study table and compact fridge to room 412, Hostel D.","vikram",None,"OPEN","2026-10-04T08:30:00Z")
]
for g in gigs:
    conn.execute("INSERT OR IGNORE INTO gigs VALUES (?,?,?,?,?,?,?,?,?,?,?)", g)
conn.commit()
print("Users:", conn.execute("SELECT count(*) FROM users").fetchone()[0])
print("Gigs:", conn.execute("SELECT count(*) FROM gigs").fetchone()[0])
for r in conn.execute("SELECT username, name, upi_id FROM users").fetchall():
    print("USER:", r)
conn.close()
