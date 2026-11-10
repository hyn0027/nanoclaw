"""NanoClaw's own entities, from the collected ncl facts."""

from ...audience import chat_lines
from ...constants import LOCAL_CHANNELS
from ..model import Instance
from ..registry import Context, environment


@environment("nanoclaw", kinds=["NanoClawAgentGroup", "NanoClawDestinationGrant", "MessagingGroup", "MessagingPlatform", "NanoClawScheduledTask"])
def nanoclaw(ctx: Context) -> None:
    """NanoClaw records (ncl): agent groups, destination grants, chats and recipients, scheduled tasks"""
    f = ctx.snap.facts
    for g in f.get("all_groups", []):
        mine = " (this session's group)" if g["id"] == f["group"]["id"] else ""
        ctx.add(Instance("NanoClawAgentGroup", g["id"], f"{g['name']} ({g['id']}, groups/{g['folder']}){mine}", None,
                         {"match": f"groups/{g['folder']}"}))
    for d in f["grants"]:
        ctx.add(Instance("NanoClawDestinationGrant", f"grant:{d['local_name']}", f"{d['local_name']} → {d['target_type']} {d['target_id']}", f["group"]["id"]))
        if d["target_type"] != "channel":
            continue
        local = d.get("channel_type") in LOCAL_CHANNELS
        platform = ctx.add(Instance("MessagingPlatform", f"platform:{d.get('channel_type')}", f"{d.get('channel_type')}" + (" (on this machine)" if local else "")))
        lines = chat_lines(ctx.snap, d["target_id"])
        mg = next((g for g in f.get("messaging_groups", []) if g["id"] == d["target_id"]), {})
        ctx.add(Instance("MessagingGroup", d["target_id"], f"{d.get('channel_type')} {lines[0]} [{'; '.join(lines[1:])}]", platform.id,
                         {"local": local, "match": mg.get("platform_id") or d["target_id"], "recipients": lines[1:]}))
    for t in f.get("tasks", []):
        ctx.add(Instance("NanoClawScheduledTask", f"task:{t['id']}",
                         f"task {t['id']}: {t.get('schedule_value') or t.get('schedule') or ''} {str(t.get('prompt') or '')[:60]}".strip(), f["group"]["id"]))
