"""看板协作管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "collab"
REQUIRED_FIELDS = ["看板编号", "看板名称", "协作成员"]
STATUS_ORDER = ["草稿", "协作中", "已发布", "已归档"]
TERMINAL_STATUS = STATUS_ORDER[-1]
# 动作 -> 允许执行的当前状态；目标状态按动作语义推进，不再把来源状态写回去
ACTION_SOURCES = {
    "发布看板": {"协作中"},
    "归档看板": {"协作中", "已发布"},
    "删除看板": {"已归档"},
}
ACTION_TARGETS = {"发布看板": "已发布", "归档看板": "已归档"}


def _next_version(entry: dict[str, Any]) -> str:
    """版本号按快照数量递增：未发布过的看板归档得到 v1，已发布的归档得到 v2。"""
    return f"v{len(entry.get('版本快照') or []) + 1}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class CollabService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("看板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["看板状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["发布时间"] = None
        entry["归档时间"] = None
        entry["版本快照"] = []
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, bool]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"协作看板 {entry_id} 不存在或已归档", False
        if action not in ACTION_SOURCES:
            return entry, f"动作「{action}」不属于看板协作管理可执行范围", False
        if action == "归档看板" and entry.get("status") == TERMINAL_STATUS:
            # 同一归档动作再次到达：返回既有归档结果，不生成第二个时间戳
            return entry, "协作看板已归档，重复归档不再生成新的动作时间", True
        if entry.get("status") not in ACTION_SOURCES[action]:
            # 动作被拒绝时看板保持原样，协作成员名册不丢、不改
            return entry, f"当前状态「{entry.get('status')}」不允许{action}，协作成员名册保持不变", False
        if action == "删除看板":
            store.rows(MODULE).remove(entry)
            return entry, "协作看板已删除看板", True

        now = _now()
        target = ACTION_TARGETS[action]
        if action == "发布看板":
            # 发布文件与当前动作时间同批写入：快照、发布时间取自同一个 now
            snapshot = {
                "版本": _next_version(entry),
                "看板状态": target,
                "协作成员": entry.get("协作成员"),
                "动作": action,
                "动作时间": now,
            }
            entry["发布时间"] = now
        else:
            # 归档快照保持归档前状态：未发布过的看板仍是「协作中」
            snapshot = {
                "版本": _next_version(entry),
                "看板状态": entry.get("status"),
                "协作成员": entry.get("协作成员"),
                "动作": action,
                "动作时间": now,
            }
            entry["归档时间"] = now
        entry.setdefault("版本快照", []).append(snapshot)
        entry["status"] = target
        entry["看板状态"] = target
        entry["pending"] = target != TERMINAL_STATUS
        return entry, f"协作看板已{action}", True

    def export_entries(self) -> list[dict[str, Any]]:
        """导出文本：每次按当前数据重新生成，不缓存旧值。

        成员名单、看板发布结果与导出文本取自同一份数据；历史快照里缺失的
        协作成员按原样保留，不用当前值回填。
        """
        items: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            snapshots = row.get("版本快照") or []
            latest = snapshots[-1] if snapshots else None
            items.append({
                "id": row.get("id"),
                "看板编号": row.get("看板编号"),
                "看板名称": row.get("看板名称"),
                "协作成员": row.get("协作成员"),
                "看板状态": row.get("status"),
                "当前版本": latest.get("版本") if latest else None,
                "动作时间": latest.get("动作时间") if latest else None,
                "发布时间": row.get("发布时间"),
                "归档时间": row.get("归档时间"),
                "版本快照": [dict(snapshot) for snapshot in snapshots],
            })
        return items
