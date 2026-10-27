"""资料失效后的发布恢复：先保存远端只读观察，再允许离开未完成发布。"""

from repopilot.storage.jobs import timestamp


def reconcile(state, store, forge, workspace):
    from .forge import remote_origin

    repository = state["issue"]["repository"]
    if remote_origin(workspace.repository()).repository.lower() != repository.lower():
        raise forge.fail("issue_repository_mismatch")
    publication = state["publication"]
    publication.setdefault("approval", dict(state["approval"]))
    publication.setdefault("commit", state["approval"]["commit"])
    # 检查失败保持in_flight及批准原状，不能把未查清的副作用当成没有发生。
    branch_head = forge.branch_head(workspace.repository(), repository, publication["branch"])
    pull = forge.find_pull_request(repository, publication["branch"], publication["base"],
                                   f"<!-- repopilot-run:{state['run_id']} -->")
    matches = branch_head == publication["commit"] and (
        pull is None or forge.pull_matches(pull, repository, publication["commit"]))
    observation = {"branch_head": branch_head, "pull_request": pull,
                   "matches_approved_commit": matches, "at": timestamp()}
    publication.setdefault("reconciliations", []).append(observation)
    store.save(state)
    # 不覆盖有分歧的分支；保留观察后仍能转人工资料更新，后续发布也会拒绝冲突。
    return observation
