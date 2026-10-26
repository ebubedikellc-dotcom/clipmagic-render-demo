"""Reuse authorized social accounts without sharing either mode's video inputs."""


def migrate(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(automation_projects)')}
    if 'destination_mode' not in columns:
        db.execute("ALTER TABLE automation_projects ADD COLUMN destination_mode TEXT NOT NULL DEFAULT 'selected'")


def connected(service, user_id):
    result = []
    for platform in sorted(service.SUPPORTED_DESTINATIONS):
        credentials = service.credentials_for(user_id, platform)
        if credentials.get('access_token') or credentials.get('api_token'):
            result.append({'platform': platform, 'account_link': credentials.get('account_link', ''), 'enabled': 1})
    return result


def destinations(service, project):
    if project['destination_mode'] == 'all_connected':
        return connected(service, project['user_id'])
    with service.database() as db:
        return [dict(row) for row in db.execute(
            'SELECT platform,account_link,enabled FROM project_destinations WHERE project_id=? AND enabled=1 ORDER BY platform',
            (project['id'],))]
