from types import SimpleNamespace

import pandas as pd
import pytest

import receipt_exclusion_rendering as rendering


class _FakeExpander:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FakeSpinner(_FakeExpander):
    pass


class _FakeStreamlit:
    def __init__(self):
        self.checkbox_value = False
        self.checkbox_labels = []
        self.buttons = {}
        self.multiselect_kwargs = {}
        self.data_editor_result = None
        self.data_editor_kwargs = {}
        self.data_editor_value = None
        self.errors = []
        self.expanders = []
        self.spinners = []
        self.rendered_text = ""
        self.session_state = {}
        self.button_clicks = set()
        self.column_config = SimpleNamespace(CheckboxColumn=lambda *args, **kwargs: kwargs)

    def dataframe(self, value, **kwargs):
        self.rendered_text += value.to_string()

    def data_editor(self, value, **kwargs):
        self.data_editor_value = value.copy()
        self.data_editor_kwargs = kwargs
        return self.data_editor_result.copy() if self.data_editor_result is not None else value.copy()

    def error(self, value):
        self.errors.append(value)

    def info(self, value):
        self.rendered_text += value

    def expander(self, label, **kwargs):
        self.expanders.append((label, kwargs))
        return _FakeExpander()

    def spinner(self, value):
        self.spinners.append(value)
        return _FakeSpinner()

    def checkbox(self, label, **kwargs):
        self.checkbox_labels.append(label)
        return self.checkbox_value

    def multiselect(self, *args, **kwargs):
        return []

    def button(self, label, **kwargs):
        self.buttons[label] = kwargs
        return label in getattr(self, "button_clicks", set())

    def markdown(self, value):
        self.rendered_text += value

    def caption(self, value):
        self.rendered_text += value

    def write(self, value):
        self.rendered_text += str(value)


def _snapshot(*, revision="revision-a", active=None, revoked=None):
    return {
        "registryRevision": revision,
        "active": active or [{
            "id": 4,
            "receiptNo": "SK2607007622",
            "sourceOrderNo": "225YTLAU6227154715",
            "exclusionKind": "receipt_type:掛賬核銷",
            "createdAt": "2026-07-23T00:00:00+08:00",
            "createdBy": "streamlit-local",
            "eventCount": 2,
        }],
        "revoked": revoked or [],
    }


def test_confirmation_requires_checkbox_before_primary_action(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)
    rendering.render_receipt_exclusion_confirmation(
        {"proposalFingerprint": "proposal-1", "candidates": [{
            "candidateId": "candidate-1", "sourceOrderNo": "31NZY6629115617",
            "receiptNo": "SK2606005393", "exclusionKind": "payment_method:TT 退款轉團款",
            "observedAmount": 1630.0, "affectedRevenue": 1270.0,
        }]},
        confirm_action=lambda payload: payload,
    )
    assert fake.checkbox_labels == [rendering.CONFIRMATION_COPY]
    assert fake.buttons["永久排除並重新預演"]["disabled"] is True


def test_governance_panel_never_exposes_quarantine_payload(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)
    rendering.render_receipt_exclusion_governance(
        {"active": [{"id": 7, "receiptNo": "SK2606005393"}], "rawPayload": {"secret": "must-not-render"}},
        preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )
    assert "must-not-render" not in fake.rendered_text


def test_governance_renders_scrollable_single_selection_table(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(), preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert fake.data_editor_kwargs["height"] == rendering.GOVERNANCE_TABLE_HEIGHT
    assert fake.data_editor_kwargs["num_rows"] == "fixed"
    assert fake.data_editor_kwargs["disabled"] == [
        "規則 ID", "收款單號", "來源單據號", "排除類型",
        "建立時間", "建立者", "稽核事件數",
    ]
    assert fake.data_editor_kwargs["key"].endswith("revision-a")
    assert "預覽撤銷 #4" not in fake.buttons
    assert fake.buttons["預覽撤銷所選規則"]["disabled"] is True


def test_governance_previews_only_the_single_selected_rule(monkeypatch):
    fake = _FakeStreamlit()
    fake.data_editor_result = pd.DataFrame([{
        "選取": True, "規則 ID": 4, "收款單號": "SK2607007622",
        "來源單據號": "225YTLAU6227154715", "排除類型": "receipt_type:掛賬核銷",
        "建立時間": "", "建立者": "streamlit-local", "稽核事件數": 2,
    }])
    fake.button_clicks = {"預覽撤銷所選規則"}
    calls = []
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(),
        preview_revoke=lambda rule_id: calls.append(rule_id) or {
            "ruleId": rule_id, "registryRevision": "revision-a",
            "status": "revocation_ready", "previewFingerprint": "preview-a", "gate": {"status": "matched"},
        },
        confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert calls == [4]
    assert fake.session_state[rendering.GOVERNANCE_PREVIEW_STATE_KEY]["ruleId"] == 4


def test_governance_preview_renders_only_scalar_gate_summary(monkeypatch):
    fake = _FakeStreamlit()
    fake.data_editor_result = pd.DataFrame([{
        "選取": True, "規則 ID": 4, "收款單號": "SK2607007622",
        "來源單據號": "225YTLAU6227154715", "排除類型": "receipt_type:掛賬核銷",
        "建立時間": "", "建立者": "streamlit-local", "稽核事件數": 2,
    }])
    fake.session_state[rendering.GOVERNANCE_PREVIEW_STATE_KEY] = {
        "ruleId": 4,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
        "gate": {
            "status": "matched",
            "matchedChecks": 3,
            "deltaAmount": 0.0,
            "quarantine": {"receiptNo": "SECRET-QUARANTINE-RECEIPT"},
            "evidence": {"fingerprint": "SECRET-EVIDENCE-FINGERPRINT"},
            "operation": {"operationId": "SECRET-OPERATION-ID"},
        },
    }
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(), preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert "matched" in fake.rendered_text
    assert "3" in fake.rendered_text
    assert "0.0" in fake.rendered_text
    for sensitive_value in (
        "quarantine", "SECRET-QUARANTINE-RECEIPT", "evidence",
        "SECRET-EVIDENCE-FINGERPRINT", "operation", "SECRET-OPERATION-ID",
    ):
        assert sensitive_value not in fake.rendered_text


def test_governance_disables_confirm_for_stale_selection_or_revision(monkeypatch):
    fake = _FakeStreamlit()
    fake.data_editor_result = pd.DataFrame([{
        "選取": True, "規則 ID": 4, "收款單號": "SK2607007622",
        "來源單據號": "225YTLAU6227154715", "排除類型": "receipt_type:掛賬核銷",
        "建立時間": "", "建立者": "streamlit-local", "稽核事件數": 2,
    }])
    fake.session_state[rendering.GOVERNANCE_PREVIEW_STATE_KEY] = {
        "ruleId": 4, "registryRevision": "old-revision",
        "status": "revocation_ready", "previewFingerprint": "preview-a",
    }
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(revision="revision-a"), preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert fake.buttons["確認撤銷所選規則"]["disabled"] is True
    assert rendering.GOVERNANCE_PREVIEW_STATE_KEY not in fake.session_state


def test_governance_rejects_multi_selection_and_shows_revoked_rules_in_expander(monkeypatch):
    fake = _FakeStreamlit()
    fake.data_editor_result = pd.DataFrame([
        {"選取": True, "規則 ID": 4},
        {"選取": True, "規則 ID": 5},
    ])
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(revoked=[{
            "id": 1, "receiptNo": "SK2606005393", "sourceOrderNo": "31NZY6629115617",
            "exclusionKind": "payment_method:TT 退款轉團款", "revokedAt": "", "revokedBy": "", "eventCount": 3,
        }]),
        preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert fake.errors == ["一次只能選取一條永久排除規則。"]
    assert fake.buttons["預覽撤銷所選規則"]["disabled"] is True
    assert fake.expanders == [("查看已撤銷規則", {"expanded": False})]


def test_governance_rows_allowlist_identity_and_hide_sensitive_fields():
    rows = rendering._governance_rows([{
        "id": 4,
        "receiptNo": "SK2607007622",
        "sourceOrderNo": "225YTLAU6227154715",
        "exclusionKind": "receipt_type:掛賬核銷",
        "createdAt": "2026-07-23T00:00:00+08:00",
        "createdBy": "streamlit-local",
        "eventCount": 2,
        "evidenceHash": "must-not-render",
        "proposalFingerprint": "must-not-render",
        "createdOperationId": "must-not-render",
    }])

    assert rows.columns.tolist() == [
        "選取", "規則 ID", "收款單號", "來源單據號", "排除類型",
        "建立時間", "建立者", "稽核事件數",
    ]
    assert rows.loc[0, "選取"] == False
    assert "must-not-render" not in rows.to_string()


def test_selected_rule_ids_preserves_no_selection_one_selection_and_multi_selection():
    edited = pd.DataFrame({"選取": [False, True, True], "規則 ID": [1, 2, 3]})

    assert rendering._selected_rule_ids(edited.iloc[0:0]) == []
    assert rendering._selected_rule_ids(edited.iloc[[1]]) == [2]
    assert rendering._selected_rule_ids(edited) == [2, 3]


def test_matching_preview_requires_same_rule_and_registry_revision():
    preview = {
        "ruleId": 4,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
    }

    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="revision-a",
    ) == preview
    assert rendering._matching_governance_preview(
        preview, rule_id=5, registry_revision="revision-a",
    ) == {}
    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="revision-b",
    ) == {}


@pytest.mark.parametrize("field", ["ruleId", "registryRevision", "status", "previewFingerprint"])
@pytest.mark.parametrize("value", [None, ""])
def test_matching_preview_fails_closed_for_missing_or_empty_required_fields(field, value):
    preview = {
        "ruleId": 4,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
    }
    if value is None:
        preview.pop(field)
    else:
        preview[field] = value

    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="revision-a",
    ) == {}


@pytest.mark.parametrize(
    "malformed_rule_id",
    ["not-an-int", object(), True, False, 4.0, 0, -1],
)
def test_matching_preview_fails_closed_for_malformed_rule_id(malformed_rule_id):
    preview = {
        "ruleId": malformed_rule_id,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
    }

    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="revision-a",
    ) == {}


def test_matching_preview_fails_closed_for_invalid_selected_rule_id_and_empty_registry_revision():
    preview = {
        "ruleId": 4,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
    }

    assert rendering._matching_governance_preview(
        preview, rule_id="not-an-int", registry_revision="revision-a",
    ) == {}
    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="",
    ) == {}


@pytest.mark.parametrize("selected_rule_id", [0, -1, True, False, 4.0, "4"])
def test_matching_preview_fails_closed_for_non_positive_or_non_integer_selected_rule_id(
    selected_rule_id,
):
    preview = {
        "ruleId": 4,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
    }

    assert rendering._matching_governance_preview(
        preview, rule_id=selected_rule_id, registry_revision="revision-a",
    ) == {}


@pytest.mark.parametrize("field", ["registryRevision", "status", "previewFingerprint"])
@pytest.mark.parametrize("value", [0, True, False, 4, [], {}, " "])
def test_matching_preview_requires_non_empty_strings_for_governance_fields(field, value):
    preview = {
        "ruleId": 4,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
    }
    preview[field] = value

    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="revision-a",
    ) == {}
