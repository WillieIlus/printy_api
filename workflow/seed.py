"""Seed roster data mirroring printy_workflow/data/printy.ts verbatim.

MANAGERS and PRINTERS here reproduce the TS constants 1:1 so the Django
persistence layer and the (ported) frontend can never drift apart. Jobs are
NOT seeded: they are created through the real workflow (quotes, payments,
assignments) so no phantom jobs ever appear.
"""

MANAGERS = [
    {"id": "m-dale", "name": "Dale Carnegie", "initials": "DC", "tag": "Client whisperer - escalations", "onTime": 96, "hue": 36},
    {"id": "m-mj", "name": "MJ DeMarco", "initials": "MD", "tag": "Fastlane runs - speed", "onTime": 92, "hue": 200},
    {"id": "m-robert", "name": "Robert Cialdini", "initials": "RC", "tag": "Approvals and persuasion", "onTime": 89, "hue": 280},
    {"id": "m-eric", "name": "Eric Ries", "initials": "ER", "tag": "Lean batches - MVPs", "onTime": 84, "hue": 150},
    {"id": "m-peter", "name": "Peter Thiel", "initials": "PT", "tag": "Zero-to-one launches", "onTime": 97, "hue": 340},
]

PRINTERS = [
    {"id": "p-north", "name": "North Press Co.", "contact": "Jon Weber", "city": "Porto", "caps": ["Offset", "Digital", "Large format"], "verified": True, "rating": 4.9, "jobsDone": 212, "onTime": 98, "initials": "NP", "hue": 32},
    {"id": "p-halftone", "name": "Halftone Works", "contact": "Iris Kohler", "city": "Gdansk", "caps": ["Digital", "Short run"], "verified": True, "rating": 4.7, "jobsDone": 168, "onTime": 94, "initials": "HW", "hue": 190},
    {"id": "p-kobo", "name": "Kobo Bindery", "contact": "Ren Sato", "city": "Lyon", "caps": ["Binding", "Finishing", "Foiling"], "verified": True, "rating": 4.8, "jobsDone": 143, "onTime": 97, "initials": "KB", "hue": 265},
    {"id": "p-magenta", "name": "Magenta Mills", "contact": "Lena Marchetti", "city": "Milan", "caps": ["Flexo", "Packaging"], "verified": False, "rating": 4.2, "jobsDone": 3, "onTime": 0, "initials": "MM", "hue": 318},
]