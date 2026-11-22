"""robots.txt parsing and matching with Google's semantics.

- Groups are selected by the most specific user-agent token that matches (falls back to '*').
- Rules support '*' wildcards and '$' end anchors; the longest matching rule wins, Allow wins ties.
- Fetch results: 200 -> rules; 4xx -> allow all; 5xx / network error -> disallow all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Rule:
    allow: bool
    path: str
    line: int
    _rx: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        pattern = re.escape(self.path).replace(r"\*", ".*")
        if pattern.endswith(r"\$"):
            pattern = pattern[:-2] + "$"
        self._rx = re.compile(pattern)

    def matches(self, target: str) -> bool:
        return bool(self._rx.match(target))

    def label(self) -> str:
        return f"{'Allow' if self.allow else 'Disallow'}: {self.path} (línea {self.line})"


@dataclass
class Group:
    agents: list[str]
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


def parse_robots(text: str) -> tuple[list[Group], list[str]]:
    groups: list[Group] = []
    sitemaps: list[str] = []
    current: Group | None = None
    last_was_agent = False
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = Group(agents=[])
                groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if key == "sitemap":
            if value:
                sitemaps.append(value)
        elif current is None:
            continue
        elif key in ("allow", "disallow"):
            if value:  # empty Disallow means "allow everything"
                current.rules.append(Rule(key == "allow", value, n))
        elif key == "crawl-delay":
            try:
                current.crawl_delay = float(value)
            except ValueError:
                pass
    return groups, sitemaps


def _path_of(url: str) -> str:
    p = urlsplit(url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


class RobotsMatcher:
    def __init__(self, text: str):
        self.text = text or ""
        self.groups, self.sitemaps = parse_robots(self.text)

    def group_for(self, token: str) -> Group | None:
        token = (token or "*").lower()
        best, best_len = None, -1
        merged: dict[str, Group] = {}
        for g in self.groups:
            for agent in g.agents:
                if agent != "*" and (token == agent or token.startswith(agent)):
                    if len(agent) > best_len:
                        best, best_len = agent, len(agent)
                merged.setdefault(agent, Group(agents=[agent]))
                merged[agent].rules.extend(g.rules)
                if g.crawl_delay is not None:
                    merged[agent].crawl_delay = g.crawl_delay
        if best is not None:
            return merged[best]
        return merged.get("*")

    def verdict(self, url: str, token: str) -> tuple[bool, Rule | None]:
        group = self.group_for(token)
        if group is None:
            return True, None
        target = _path_of(url)
        best: Rule | None = None
        for rule in group.rules:
            if rule.matches(target):
                if best is None or len(rule.path) > len(best.path) or (len(rule.path) == len(best.path) and rule.allow):
                    best = rule
        return (True, best) if best is None or best.allow else (False, best)


@dataclass
class RobotsRules:
    origin: str
    status: str  # "ok", "missing", "unavailable"
    text: str = ""
    reason: str = ""
    sitemaps: list[str] = field(default_factory=list)
    _matcher: RobotsMatcher | None = None
    _allow_all: bool = False
    _disallow_all: bool = False

    @classmethod
    def parse(cls, origin: str, text: str) -> "RobotsRules":
        m = RobotsMatcher(text)
        return cls(origin=origin, status="ok", text=text, sitemaps=m.sitemaps, _matcher=m)

    @classmethod
    def allow_all(cls, origin: str, reason: str) -> "RobotsRules":
        return cls(origin=origin, status="missing", reason=reason, _allow_all=True)

    @classmethod
    def disallow_all(cls, origin: str, reason: str) -> "RobotsRules":
        return cls(origin=origin, status="unavailable", reason=reason, _disallow_all=True)

    def allowed(self, url: str, token: str) -> bool:
        if self._allow_all:
            return True
        if self._disallow_all or self._matcher is None:
            return False
        return self._matcher.verdict(url, _token(token))[0]

    def crawl_delay(self, token: str) -> float:
        if self._matcher is None:
            return 0.0
        g = self._matcher.group_for(_token(token))
        return min(float(g.crawl_delay or 0), 60.0) if g else 0.0

    def snapshot(self) -> dict:
        return {"status": self.status, "reason": self.reason, "text": self.text[:50000]}


def _token(user_agent: str) -> str:
    """'LetsCrawlit/0.1 (...)' -> 'letscrawl'. Explicit tokens ('Googlebot') pass through."""
    return (user_agent or "*").split("/")[0].split()[0].lower() if user_agent else "*"


def test_urls(robots_text: str, urls: list[str], token: str) -> list[dict]:
    """Robots.txt tester: verdict and deciding rule for each URL (no network)."""
    m = RobotsMatcher(robots_text)
    out = []
    for u in urls:
        ok, rule = m.verdict(u, _token(token))
        out.append({"url": u, "allowed": ok, "rule": rule.label() if rule else "Ninguna regla coincide (permitido)"})
    return out
