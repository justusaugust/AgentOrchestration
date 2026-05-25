"""Safe data-table projection and markup rendering."""

from dataclasses import dataclass
from html import escape
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


@dataclass(frozen=True)
class TableColumn:
    key: str
    label: str
    sensitive: bool = False
    permission: Optional[str] = None


TABLE_SCHEMAS: Dict[str, Sequence[TableColumn]] = {
    "agents": (
        TableColumn("id", "Agent ID"),
        TableColumn("name", "Name"),
        TableColumn("type", "Type"),
        TableColumn("status", "Status"),
        TableColumn(
            "config",
            "Config",
            sensitive=True,
            permission="agents:sensitive",
        ),
        TableColumn(
            "metrics",
            "Metrics",
            sensitive=True,
            permission="agents:sensitive",
        ),
    ),
    "tasks": (
        TableColumn("id", "Task ID"),
        TableColumn("title", "Title"),
        TableColumn("status", "Status"),
        TableColumn("assignee", "Assignee"),
        TableColumn(
            "customer_email",
            "Customer Email",
            sensitive=True,
            permission="tasks:sensitive",
        ),
        TableColumn(
            "internal_notes",
            "Internal Notes",
            sensitive=True,
            permission="tasks:sensitive",
        ),
        TableColumn(
            "secret_token",
            "Secret Token",
            sensitive=True,
            permission="tasks:secrets",
        ),
    ),
    "members": (
        TableColumn("id", "Member ID"),
        TableColumn("name", "Name"),
        TableColumn("role", "Role"),
        TableColumn(
            "email",
            "Email",
            sensitive=True,
            permission="members:sensitive",
        ),
        TableColumn(
            "salary",
            "Salary",
            sensitive=True,
            permission="members:compensation",
        ),
        TableColumn(
            "api_key",
            "API Key",
            sensitive=True,
            permission="members:secrets",
        ),
    ),
}


def _schema_for(table_name: str) -> Sequence[TableColumn]:
    try:
        return TABLE_SCHEMAS[table_name]
    except KeyError as exc:
        raise ValueError(f"unknown table: {table_name}") from exc


def _permission_set(permissions: Optional[Iterable[str]]) -> Set[str]:
    return set(permissions or ())


def _is_authorized(column: TableColumn, permissions: Set[str]) -> bool:
    return column.permission is None or column.permission in permissions


def visible_columns(
    table_name: str,
    requested_columns: Optional[Iterable[str]] = None,
    permissions: Optional[Iterable[str]] = None,
) -> List[TableColumn]:
    """Return authorized columns explicitly visible in the UI."""

    schema = _schema_for(table_name)
    allowed_permissions = _permission_set(permissions)
    requested = (
        set(requested_columns)
        if requested_columns is not None
        else None
    )
    unknown = (
        requested.difference(column.key for column in schema)
        if requested is not None
        else set()
    )
    if unknown:
        unknown_columns = ", ".join(sorted(unknown))
        raise ValueError(
            f"unknown columns for {table_name}: {unknown_columns}"
        )

    columns = []
    for column in schema:
        if requested is not None and column.key not in requested:
            continue
        if requested is None and column.sensitive:
            continue
        if not _is_authorized(column, allowed_permissions):
            continue
        columns.append(column)
    return columns


def project_rows(
    table_name: str,
    rows: Iterable[Mapping[str, Any]],
    requested_columns: Optional[Iterable[str]] = None,
    permissions: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Strip disallowed fields before rows reach markup or clients."""

    columns = visible_columns(table_name, requested_columns, permissions)
    column_keys = [column.key for column in columns]
    return [{key: row.get(key, "") for key in column_keys} for row in rows]


def column_toggle_payload(
    table_name: str,
    permissions: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Describe only columns a viewer may request through the visibility UI."""

    allowed_permissions = _permission_set(permissions)
    return [
        {
            "key": column.key,
            "label": column.label,
            "sensitive": column.sensitive,
        }
        for column in _schema_for(table_name)
        if _is_authorized(column, allowed_permissions)
    ]


def render_table_html(
    table_name: str,
    rows: Iterable[Mapping[str, Any]],
    requested_columns: Optional[Iterable[str]] = None,
    permissions: Optional[Iterable[str]] = None,
) -> str:
    columns = visible_columns(table_name, requested_columns, permissions)
    projected = project_rows(
        table_name,
        rows,
        [column.key for column in columns],
        permissions,
    )

    header = "".join(f"<th>{escape(column.label)}</th>" for column in columns)
    body_rows = []
    for row in projected:
        cells = "".join(
            f"<td>{escape(str(row[column.key]))}</td>"
            for column in columns
        )
        body_rows.append(f"<tr>{cells}</tr>")
    body = "".join(body_rows)
    return (
        "<table><thead><tr>"
        f"{header}</tr></thead><tbody>{body}</tbody></table>"
    )
