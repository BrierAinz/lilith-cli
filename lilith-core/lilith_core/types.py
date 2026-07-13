"""Base types for Yggdrasil ecosystem."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Realm(Enum):
    """The Nine Realms of Yggdrasil."""

    ASGARD = "Asgard"
    ALFHEIM = "Alfheim"
    VANAHEIM = "Vanaheim"
    MUSPELHEIM = "Muspelheim"
    NIFLHEIM = "Niflheim"
    SVARTALFHEIM = "Svartalfheim"
    MIDGARD = "Midgard"
    HELHEIM = "Helheim"
    JOTUNHEIM = "Jotunheim"


class Status(Enum):
    """Project status."""

    ACTIVE = "active"
    WIP = "wip"
    ARCHIVED = "archived"
    DEAD = "dead"


@dataclass
class Project:
    """A project in the Yggdrasil ecosystem."""

    name: str
    realm: Realm
    version: str = "0.1.0"
    description: str = ""
    status: Status = Status.ACTIVE
    created: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Agent:
    """An AI agent in the ecosystem."""

    name: str
    version: str
    permissions: list[str] = field(default_factory=list)
    active: bool = False


@dataclass
class Service:
    """A running service."""

    name: str
    port: int
    host: str = "localhost"
    active: bool = False
    pid: int | None = None
