from app.services.health import Check, default_checks


def get_health_checks() -> dict[str, Check]:
    return default_checks()
