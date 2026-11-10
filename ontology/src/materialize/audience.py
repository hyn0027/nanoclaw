"""Who is concretely behind a destination grant, from NanoClaw's own records.

A channel destination is one messaging group (a DM or a group chat on one
platform). For a DM, the recipient is the user whose DM cache (user_dms) points
at that chat, or for a local channel the user the chat names (e.g. cli:local);
for a group chat, everyone in the platform channel receives it and NanoClaw
keeps no member list, so only the users it knows in that chat are named.
An agent destination is another agent group; what it is wired to, and who may
use it, are listed one level deep. Users are shown with their NanoClaw roles and
agent-group memberships. Everything comes from collected facts.
"""

from .snapshot import Snapshot


def _user(snap: Snapshot, user_id: str) -> str:
    f = snap.facts
    user = next((u for u in f.get("users", []) if u["id"] == user_id), None)
    name = (user or {}).get("display_name") or user_id
    group_names = {g["id"]: g["name"] for g in f.get("all_groups", [])}
    roles = [r["role"] + (f" of {group_names.get(r['agent_group_id'], r['agent_group_id'])}" if r.get("agent_group_id") else "")
             for r in f.get("roles", []) if r["user_id"] == user_id]
    member = [group_names.get(m["agent_group_id"], m["agent_group_id"]) for m in f.get("all_members", []) if m["user_id"] == user_id]
    tags = roles + ([f"member of {', '.join(sorted(member))}"] if member else [])
    return f"{name} ({user_id})" + (f": {'; '.join(tags)}" if tags else "")


def chat_lines(snap: Snapshot, messaging_group_id: str) -> list[str]:
    """The chat and the people in it, as far as NanoClaw records them."""
    mg = next((g for g in snap.facts.get("messaging_groups", []) if g["id"] == messaging_group_id), None)
    if mg is None:
        return [f"chat: {messaging_group_id} (not found)"]
    dm_users = [d["user_id"] for d in snap.facts.get("user_dms", []) if d["messaging_group_id"] == messaging_group_id]
    if not mg.get("is_group") and not dm_users:
        # A local channel's chat is its user: user id "<channel>:<platform id>" (e.g. cli:local).
        own = f"{mg['channel_type']}:{mg['platform_id']}"
        dm_users = [u["id"] for u in snap.facts.get("users", []) if u["id"] == own]
    if not mg.get("is_group"):
        lines = [f"DM {mg['platform_id']}"]
        if dm_users:
            lines += [f"recipient: {_user(snap, u)}" for u in dm_users]
            lines.append(f"recipients: {len(dm_users)} (only {'this user' if len(dm_users) == 1 else 'these users'})")
        else:
            lines.append("recipient: the DM's other party (no user_dms record)")
        return lines
    lines = [f"group chat {mg['platform_id']}" + (f" ({mg['name']})" if mg.get("name") else "")]
    lines.append("recipients: everyone in the platform channel (NanoClaw keeps no member list)")
    lines += [f"known: {_user(snap, u)}" for u in dm_users]
    return lines


def destination_lines(snap: Snapshot, dest: dict) -> list[str]:
    """Indented-ready lines describing who a destination grant reaches."""
    if dest["target_type"] == "channel":
        return chat_lines(snap, dest["target_id"])
    f = snap.facts
    group = next((g for g in f.get("all_groups", []) if g["id"] == dest["target_id"]), None)
    lines = [f"agent group {group['name'] if group else dest['target_id']} ({dest['target_id']})"]
    for w in (w for w in f.get("all_wirings", []) if w["agent_group_id"] == dest["target_id"]):
        mg = next((g for g in f.get("messaging_groups", []) if g["id"] == w["messaging_group_id"]), {})
        lines.append(f"its chat: {mg.get('channel_type', '?')} {mg.get('name') or mg.get('platform_id') or w['messaging_group_id']}")
        lines += [f"  {line}" for line in chat_lines(snap, w["messaging_group_id"])[1:]]
    lines += [f"its member: {_user(snap, m['user_id'])}" for m in f.get("all_members", []) if m["agent_group_id"] == dest["target_id"]]
    return lines


def destination_location(snap: Snapshot, dest: dict) -> str:
    """Location cell for a destination: the grant, then who is behind it."""
    head = f"{dest.get('channel_type') or 'agent'}: {dest.get('display_name') or dest['local_name']}"
    return "\n".join([head, *[f"  {line}" for line in destination_lines(snap, dest)]])
