import pandas as pd


def test_read_excel_source_accepts_preloaded_dataframe_tuple_without_re_reading(monkeypatch):
    import pipeline

    def fail_read_excel(*args, **kwargs):
        raise AssertionError("pd.read_excel should not be called for preloaded frames")

    monkeypatch.setattr(pipeline.pd, "read_excel", fail_read_excel)
    source = pd.DataFrame({"來源單據號": ["17001"], "收款時間": ["2026-06-15"]})

    frame, name = pipeline._read_excel_source(("旅行團0628all.xlsx", source))

    assert name == "旅行團0628all.xlsx"
    assert frame.equals(source)
    assert frame is not source
