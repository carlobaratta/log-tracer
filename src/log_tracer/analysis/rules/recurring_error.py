"""RUL-1 recurring_error: the same error pattern occurring several times."""

from types import MappingProxyType

from log_tracer.analysis.context import AnalysisContext, finding_id
from log_tracer.config import RecurringErrorConfig
from log_tracer.models import ErrorGroup, Finding, RuleName, RuleOutcome, Severity

RULE = RuleName.RECURRING_ERROR


def run(context: AnalysisContext, config: RecurringErrorConfig) -> RuleOutcome:
    findings = tuple(
        _finding(group, config)
        for group in context.error_groups
        if group.count >= config.min_occurrences
    )
    return RuleOutcome(rule=RULE, status="ok", reason=None, findings=findings)


def _finding(group: ErrorGroup, config: RecurringErrorConfig) -> Finding:
    high = group.has_critical or group.count >= config.high_count
    origin = f"service '{group.service}'" if group.service else "events without a service"
    return Finding(
        id=finding_id(RULE, group.service, group.fingerprint),
        rule=RULE,
        severity=Severity.HIGH if high else Severity.MEDIUM,
        title=f"Recurring error in {group.service or 'unknown service'}: {group.fingerprint}",
        description=(
            f'{group.count} error events from {origin} match the pattern "{group.fingerprint}".'
        ),
        evidence=MappingProxyType(
            {
                "service": group.service,
                "fingerprint": group.fingerprint,
                "count": group.count,
                "min_occurrences": config.min_occurrences,
                "high_count": config.high_count,
                "has_critical": group.has_critical,
                "first_seen": group.first_seen,
                "last_seen": group.last_seen,
                "correlation_groups": group.correlation_groups,
            }
        ),
        event_count=group.count,
        events=group.events,
        correlation_ids=group.correlation_ids,
    )
