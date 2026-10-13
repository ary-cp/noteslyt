"""Deterministic spelling correction regressions; no external services."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.transcript_spellings import validate_spellings,correct_spellings,save_spellings,load_spellings

def main():
    rules=validate_spellings({"Myra":"Mira"})
    assert correct_spellings("Myra leads; MYRA's meeting. Myranda attends.",rules)=="Mira leads; Mira's meeting. Myranda attends."
    assert correct_spellings("Meera and Maya attend.",rules)=="Meera and Maya attend."
    rules=validate_spellings({"Sam":"Sam Smith"})
    once=correct_spellings("Sam spoke to Sam Smith.",rules)
    assert once=="Sam Smith spoke to Sam Smith." and correct_spellings(once,rules)==once
    for invalid in ({}, {"A":"B","B":"A"}, {"a":"b","A":"c"}, {"":"Mira"}):
        try:validate_spellings(invalid)
        except ValueError:pass
        else:raise AssertionError(invalid)
    with tempfile.TemporaryDirectory() as tmp:
        a=Path(tmp)/"a.wav"; b=Path(tmp)/"b.wav"
        save_spellings(a,validate_spellings({"Myra":"Mira"}))
        assert load_spellings(a)=={"myra":"Mira"} and load_spellings(b)=={}
    from app.ingestion.units import SourceUnit, units_from_markdown
    from app.ingestion.pipeline import render_parsed_markdown
    unit=SourceUnit(index=1,text="Myra leads the project.",locator_kind="segment",locator_value="1:30",start_ts=90.125,end_ts=105.5)
    unit.text=correct_spellings(unit.text,validate_spellings({"Myra":"Mira"}))
    markdown,truncated=render_parsed_markdown("lesson.wav",[unit],400000,parser="whisper")
    recovered,_=units_from_markdown(markdown)
    assert not truncated and recovered[0].text=="Mira leads the project."
    assert recovered[0].start_ts==90.125 and recovered[0].end_ts==105.5
    print("TRANSCRIPT SPELLINGS VERIFIED: boundaries, no guesses, idempotence, validation, source isolation")
if __name__=="__main__":main()
