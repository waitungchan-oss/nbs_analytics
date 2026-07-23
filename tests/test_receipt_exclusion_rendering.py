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

    def dataframe(self, value, **kwargs):
        self.rendered_text += value.to_string()

    def data_editor(self, value, **kwargs):
        self.data_editor_value = value.copy()
        self.data_editor_kwargs = kwargs
        return self.data_editor_result.copy() if self.data_editor_result is not None else value.copy()

    def error(self, value):
        self.errors.append(value)

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
        return False

    def markdown(self, value):
        self.rendered_text += value

    def caption(self, value):
        self.rendered_text += value

    def write(self, value):
        self.rendered_text += str(value)


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


@pytest.mark.parametrize("malformed_rule_id", ["not-an-int", object()])
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
