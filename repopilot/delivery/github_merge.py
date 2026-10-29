"""GitHub merge observations and asynchronous requests; no model or Git shell access."""

from urllib.parse import quote
import re

import httpx


class GitHubMerge:
    def __init__(self, forge, repository, number):
        if forge.name != 'github' or forge.host != 'github.com':
            raise forge.fail('github_merge_only')
        if (not isinstance(repository, str) or
                not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or
                '..' in repository or type(number) is not int or number < 1):
            raise forge.fail('invalid_merge_identity')
        self.forge = forge
        self.prefix = f'/repos/{repository}'
        self.pull_path = f'{self.prefix}/pulls/{number}'

    def pull(self):
        return self.forge.request('GET', self.pull_path)

    def pages(self, path, *, field=None, params=None):
        result = []
        for page in range(1, 6):
            response = self.forge.request('GET', path,
                params={**(params or {}), 'per_page': 100, 'page': page})
            values = response.get(field) if field and isinstance(response, dict) else response
            if not isinstance(values, list):
                raise self.forge.fail('github_invalid_response')
            result.extend(values)
            if len(values) < 100:
                return result
        raise self.forge.fail('merge_observation_scan_limit')

    def reviews(self):
        return self.pages(self.pull_path + '/reviews')

    def checks(self, commit):
        runs = self.pages(f'{self.prefix}/commits/{commit}/check-runs', field='check_runs',
                          params={'filter': 'latest'})
        statuses = self.pages(f'{self.prefix}/commits/{commit}/statuses')
        return runs, statuses

    def protection(self, branch):
        try:
            return self.forge.request('GET', f'{self.prefix}/branches/{quote(branch, safe="")}/protection')
        except Exception as exc:
            if getattr(exc, 'status', None) == 404:
                raise self.forge.fail('strict_branch_protection_required') from None
            raise

    def compare(self, base, head):
        if not re.fullmatch(r'[0-9a-f]{40,64}', head):
            head = self.forge.commit_sha(self.prefix.removeprefix('/repos/'), head)
        if not all(re.fullmatch(r'[0-9a-f]{40,64}', value) for value in (base, head)):
            raise self.forge.fail('invalid_comparison_commit')
        # Forge.request rejects every '..' to prevent path traversal. Keep that
        # boundary unchanged; this endpoint permits only two validated full SHAs.
        try:
            response = self.forge.client.request('GET', f'{self.prefix}/compare/{base}...{head}')
        except httpx.HTTPError:
            raise self.forge.fail('github_transport_error') from None
        if response.status_code != 200:
            raise self.forge.Error(f'github_http_{response.status_code}', status=response.status_code,
                                   retry_after=self.forge.retry_after(response))
        try:
            result = response.json()
        except ValueError:
            raise self.forge.fail('github_invalid_response') from None
        if not isinstance(result, dict):
            raise self.forge.fail('github_invalid_response')
        return result

    def submit(self, commit):
        # The expected head and bypass_rules=false are enforced by GitHub at execution time.
        # A 202 / 409 acknowledges a request, never a completed merge.
        try:
            response = self.forge.client.request('PUT', self.pull_path + '/merge-async',
                headers={'X-GitHub-Api-Version': '2026-03-10'},
                json={'sha': commit, 'merge_method': 'merge', 'merge_action': 'direct_merge',
                      'bypass_rules': False})
        except httpx.HTTPError:
            raise self.forge.fail('github_merge_transport_unknown') from None
        if response.status_code not in {200, 202, 409}:
            raise self.forge.Error(f'github_http_{response.status_code}',
                status=response.status_code, retry_after=self.forge.retry_after(response))
        try:
            result = response.json()
        except ValueError:
            raise self.forge.fail('github_invalid_response') from None
        if not isinstance(result, dict):
            raise self.forge.fail('github_invalid_response')
        return result

    def result(self, identifier):
        if not re.fullmatch(r'[A-Za-z0-9-]{1,100}', identifier):
            raise self.forge.fail('invalid_merge_request_id')
        return self.forge.request('GET', self.pull_path + '/merge-async/' + identifier,
                                  headers={'X-GitHub-Api-Version': '2026-03-10'})
