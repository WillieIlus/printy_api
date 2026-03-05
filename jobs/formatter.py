"""
JobShare WhatsApp formatter — safe, public-facing message.
No internal costs or sensitive data.
"""
from jobs.models import JobRequest


def format_job_for_whatsapp_share(job: JobRequest) -> str:
    """
    Generate shareable WhatsApp message for a job request.
    Only includes safe, public fields (no internal costs).
    """
    lines = []
    lines.append(f"📋 *{job.title}*")
    lines.append("")
    if job.specs:
        for key, val in job.specs.items():
            if val is not None and val != "":
                lines.append(f"• {key}: {val}")
        lines.append("")
    if job.machine_type:
        lines.append(f"🖨️ Machine: {job.get_machine_type_display()}")
    if job.finishing_capabilities:
        lines.append(f"✂️ Finishing: {', '.join(job.finishing_capabilities)}")
    if job.location:
        lines.append(f"📍 {job.location}")
    if job.deadline:
        lines.append(f"⏰ Deadline: {job.deadline.strftime('%d %b %Y')}")
    lines.append("")
    lines.append("Interested? Claim this job on Printy.")
    return "\n".join(lines)
