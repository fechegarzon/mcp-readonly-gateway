import copy

import pytest

from mcp_readonly_gateway import Policy, PolicyError, load_policy

from .conftest import BASE_POLICY, EXAMPLES


def test_example_policy_loads():
    policy = load_policy(EXAMPLES / "policy.yaml")
    assert policy.tools_for("analyst") == ["list_messages", "search_messages", "read_message"]
    assert policy.tools_for("ops-lead")[-1] == "create_draft"
    assert policy.audit_path.is_absolute()


def _with(**changes):
    data = copy.deepcopy(BASE_POLICY)
    data.update(changes)
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_with(extra=1), "unknown keys"),
        (_with(tools={"send_email": {}}), "Unknown tool"),
        (_with(tools={"gateway_info": {}}), "always on"),
        (_with(tools={"list_messages": {"max_results": 0}}), "positive integer"),
        (_with(tools={"list_messages": {"max_reslts": 5}}), "unknown keys"),
        (_with(labels={"readable": []}), "at least one label"),
        (_with(callers={}), "at least one caller"),
        (_with(writes={"enabled": "yes"}), "true or false"),
        (_with(version=2), "Unsupported policy version"),
    ],
)
def test_invalid_policies_fail_loudly(data, message):
    with pytest.raises(PolicyError, match=message):
        Policy.from_dict(data)


def test_scope_cannot_reach_outside_the_allowlist():
    data = _with(
        tools={"list_messages": {}},
        callers={"analyst": {"tools": ["list_messages", "read_message"]}},
    )
    with pytest.raises(PolicyError, match="not in the tools allowlist"):
        Policy.from_dict(data)
