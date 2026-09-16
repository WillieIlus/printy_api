"""Faithful Python port of the pure transition functions in
printy_workflow/data/printy.ts.

The TS originals take a `Job` (camelCase object) and return a new `Job`. These
functions take the same camelCase dict and return the same dict, so the rules
can be audited line-for-line against the source of truth. `mgrs`/`prns` are
id-keyed dicts built from seed.py.

Potential drift from the original to check if re-syncing from TS:
  * money() here reproduces toLocaleString("en-US") but only for whole-dollar
    amounts (seed data has no decimals).
  * toLocaleString uses en-US grouping; matches the seed layout exactly.
"""

NOW_AT = "Today - 11:47"


def money(n):
    return "$" + "{:,}".format(int(n))


def hist(stage, actor, actor_role, note):
    return {"stage": stage, "actor": actor, "actorRole": actor_role, "at": NOW_AT, "note": note}


def log(job, who, text):
    return [{"at": NOW_AT, "who": who, "text": text, "jobCode": job["code"]}, *job["feed"]]


def approve_artwork(job):
    if job["stage"] != "approval":
        return job
    return {
        **job,
        "status": "on-track" if job["status"] == "at-risk" else job["status"],
        "stage": "payment",
        "owner": {"name": job["buyerName"], "role": "Buyer", "action": "Release payment of {}".format(money(job["value"])), "waitingHrs": 0, "slaHrs": 4},
        "history": [*job["history"], hist("approval", job["buyerName"], "Buyer", "Artwork approved - proof final")],
        "feed": log(job, job["buyerName"], "approved the artwork - job moved to payment"),
    }


def request_changes(job):
    if job["stage"] != "approval":
        return job
    return {
        **job,
        "status": "at-risk",
        "stage": "artwork",
        "owner": {"name": "Mara Ivers - Studio", "role": "Studio", "action": "Revise artwork - proof v3", "waitingHrs": 0, "slaHrs": 24},
        "feed": log(job, job["buyerName"], "requested changes - job returned to Studio for proof v3"),
    }


def pay_job(job, mgrs):
    if job["stage"] != "payment":
        return job
    manager = mgrs[job["managerId"]]
    return {
        **job,
        "custody": "held",
        "stage": "production",
        "owner": {"name": manager["name"], "role": "Manager", "action": "Assign a printer to the job", "waitingHrs": 0, "slaHrs": 24},
        "history": [*job["history"], hist("payment", job["buyerName"], "Buyer", "{} moved into Printy Custody".format(money(job["value"])))],
        "feed": log(job, job["buyerName"], "paid {} - funds now held in Printy Custody".format(money(job["value"]))),
    }


def assign_printer(job, printer_id, mgrs, prns):
    if job["stage"] != "production" or job["printerId"]:
        return job
    p = prns[printer_id]
    m = mgrs[job["managerId"]]
    return {
        **job,
        "printerId": printer_id,
        "status": "on-track" if job["status"] == "overdue" else job["status"],
        "stage": "printing",
        "press": "accept",
        "owner": {"name": "{} - {}".format(p["contact"], p["name"]), "role": "Printer", "action": "Accept job request", "waitingHrs": 0, "slaHrs": 6},
        "history": [*job["history"], hist("production", m["name"], "Manager", "Brief, dielines and assets sent to {}".format(p["name"]))],
        "feed": log(job, m["name"], "assigned {} - waiting on printer acceptance".format(p["name"])),
    }


def advance_press(job, prns):
    p = prns.get(job["printerId"]) if job["printerId"] else None
    who = p["contact"] if p else "Printer"
    pname = " - {}".format(p["name"]) if p else ""

    if job["stage"] == "printing" and job["press"] == "accept":
        return {**job, "press": "ready", "owner": {"name": who + pname, "role": "Printer", "action": "Plates and paper staged - start the run", "waitingHrs": 0, "slaHrs": 8}, "feed": log(job, who, "accepted the job - plates, paper and ink allocated")}

    if job["stage"] == "printing" and job["press"] == "ready":
        return {**job, "press": "active", "progress": 14, "owner": {"name": who + pname, "role": "Printer", "action": "Press running - make-ready sheets clean", "waitingHrs": 0, "slaHrs": 16}, "feed": log(job, who, "started the press - make-ready approved")}

    if job["stage"] == "printing" and job["press"] == "active":
        finish_action = "Start finishing - {}".format(job["specs"]["finish"].split("-")[0].strip().lower())
        return {**job, "stage": "finishing", "press": "ready", "progress": None, "owner": {"name": who + pname, "role": "Printer", "action": finish_action, "waitingHrs": 0, "slaHrs": 12}, "history": [*job["history"], hist("printing", who, "Printer", "{} units off press - register held".format("{:,}".format(job["qty"])))], "feed": log(job, who, "printing complete - skid moved to finishing bay")}

    if job["stage"] == "finishing" and job["press"] == "ready":
        return {**job, "press": "active", "progress": 32, "owner": {"name": who + pname, "role": "Printer", "action": "Finishing line running - first-offs checked", "waitingHrs": 0, "slaHrs": 12}, "feed": log(job, who, "started finishing - first-offs signed off")}

    if job["stage"] == "finishing" and job["press"] == "active":
        return {**job, "stage": "qc", "press": "ready", "progress": None, "owner": {"name": who + pname, "role": "Printer", "action": "Run quality control - pull samples", "waitingHrs": 0, "slaHrs": 8}, "history": [*job["history"], hist("finishing", who, "Printer", job["specs"]["finish"])], "feed": log(job, who, "finishing complete - units banded and queued for QC")}

    if job["stage"] == "qc" and job["press"] == "ready":
        return {**job, "press": "active", "progress": 60, "owner": {"name": who + pname, "role": "Printer", "action": "Sampling 1-in-200 - densitometer on bench", "waitingHrs": 0, "slaHrs": 8}, "feed": log(job, who, "QC sampling started")}

    if job["stage"] == "qc" and job["press"] == "active":
        return {**job, "stage": "delivery", "press": None, "progress": None, "owner": {"name": "Marta K. - SwiftLine", "role": "Courier", "action": "Collect from dispatch bay 2 and deliver", "waitingHrs": 0, "slaHrs": 12}, "eta": "Tomorrow - 14:00", "history": [*job["history"], hist("qc", who, "Printer", "Passed - zero defects in pull")], "feed": log(job, who, "QC passed - pallets wrapped, courier booked")}

    return job


def confirm_delivery(job, prns):
    if job["stage"] != "delivery":
        return job
    p = prns.get(job["printerId"]) if job["printerId"] else None
    printer_name = p["name"] if p else "printer"
    return {
        **job,
        "stage": "completed",
        "status": "completed",
        "custody": "released",
        "owner": {"name": "Printy Custody", "role": "Escrow", "action": "Funds released - job archived", "waitingHrs": 0, "slaHrs": 0},
        "eta": "Delivered today",
        "history": [*job["history"], hist("delivery", "Marta K.", "Courier", "Delivered and signed at buyer dock"), hist("completed", "Printy Custody", "Escrow", "Released {} to {}".format(money(job["value"]), printer_name))],
        "feed": log(job, job["buyerName"], "confirmed delivery - {} released from custody".format(money(job["value"]))),
    }


def resolve_dispute(job, mgrs):
    if job["status"] != "disputed":
        return job
    m = mgrs[job["managerId"]]
    dispute = {**job["dispute"], "resolved": True} if job["dispute"] else None
    return {
        **job,
        "status": "on-track",
        "dispute": dispute,
        "press": "ready",
        "eta": "Reprint - Fri 22 Nov",
        "owner": {"name": m["name"], "role": "Manager", "action": "Supervise reprint at night rate - verified profile", "waitingHrs": 0, "slaHrs": 24},
        "feed": log(job, m["name"], "dispute resolved - reprint approved"),
    }


def nudge(job, by):
    return {**job, "feed": log(job, by, "nudged {} - {}".format(job["owner"]["name"], job["owner"]["action"]))}