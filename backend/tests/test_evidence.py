from app.models import AnalysisVerdict, EvidenceQuote, Passage, Source
from app.validation import validate_verdict


def source(kind='fetched_article'):
    return Source(id='s1', title='Report', url='https://example.org/report',
                  excerpt='A snippet mentions orbit changes.', evidence_kind=kind,
                  passages=[Passage(id='s1-p1', text='DART changed the orbit.\n Measurements remain uncertain.')])


def check(quote, sid='s1', pid='s1-p1', kind='fetched_article'):
    return validate_verdict(AnalysisVerdict('supported', 'The report describes a change [s1].', ['s1'],
        [EvidenceQuote(source_id=sid, passage_id=pid, quote=quote)]), [source(kind)])


def test_exact_passage_and_whitespace_only_matching():
    result = check('orbit. Measurements')
    assert result.evidence_quotes[0].wording_verified
    assert result.status == 'supported'
    for bad in ['Orbit.', 'orbit!', 'DART changed measurements']:
        result = check(bad)
        assert result.status == 'insufficient_evidence' and not result.evidence_quotes


def test_unknown_source_or_passage_rejected():
    assert check('DART', sid='s9').status == 'insufficient_evidence'
    assert check('DART', pid='s1-p9').status == 'insufficient_evidence'
    assert check('DART', sid='S1').status == 'insufficient_evidence'


def test_snippet_wording_does_not_become_fetched_passage():
    result = check('snippet mentions', pid=None, kind='social_snippet')
    assert result.evidence_quotes[0].passage_id is None
    assert check('Measurements', pid=None, kind='search_snippet').status == 'insufficient_evidence'
    assert check('snippet mentions', pid=None, kind='unknown').status == 'insufficient_evidence'


def test_unmatched_prose_quotation_downgrades():
    result = validate_verdict(AnalysisVerdict('supported', 'It says “DART failed” [s1].', ['s1']), [source()])
    assert result.status == 'insufficient_evidence'


def test_snippets_and_originating_article_alone_cannot_strengthen_finding():
    snippets = [Source(id='s1', title='Search', url='https://example.org/a', evidence_kind='search_snippet', excerpt='Orbit changed')]
    assert validate_verdict(AnalysisVerdict('supported', 'Orbit changed [s1].', ['s1']), snippets).status == 'insufficient_evidence'
    seed = source().model_copy(update={'provenance': {'role': 'originating_article'}})
    assert validate_verdict(AnalysisVerdict('supported', 'Orbit changed [s1].', ['s1']), [seed]).status == 'insufficient_evidence'


def test_fetched_body_does_not_relabel_excerpt_quotation():
    fetched = source().model_copy(update={'provenance': {'excerpt_kind': 'search_snippet'}})
    result = validate_verdict(AnalysisVerdict('insufficient_evidence', 'Snippet says so [s1].', ['s1'],
        [EvidenceQuote(source_id='s1', quote='snippet mentions')]), [fetched])
    assert result.evidence_quotes[0].material_kind == 'search_snippet'
    assert result.evidence_quotes[0].wording_verified


def test_single_quoted_prose_and_nested_explanation_cannot_invent_quotations():
    assert validate_verdict(AnalysisVerdict('supported', "It says 'DART failed' [s1].", ['s1']), [source()]).status == 'insufficient_evidence'
    quote = EvidenceQuote(source_id='s1', passage_id='s1-p1', quote='DART changed', explanation='It says “failed”.')
    result = validate_verdict(AnalysisVerdict('supported', 'Changed [s1].', ['s1'], [quote]), [source()])
    assert result.status == 'insufficient_evidence' and not result.evidence_quotes


async def test_model_receives_the_exact_bounded_passages(monkeypatch):
    import json
    from app import gemini_client
    seen = []
    async def generate(http, settings, system, prompt, schema):
        seen.append(prompt)
        return {'status': 'supported', 'explanation': 'Changed [s1].', 'cited_source_ids': ['s1'],
                'evidence_quotes': [{'source_id': 's1', 'passage_id': 's1-p1', 'quote': 'Measurements remain uncertain.', 'explanation': 'Magnitude remains uncertain.'}]}
    monkeypatch.setattr(gemini_client, '_generate_json', generate)
    evidence = source()
    raw = await gemini_client.analyze_claim(None, None, 'DART changed the orbit.', [evidence])
    supplied = json.loads(seen[0].split('<sources>')[1].split('</sources>')[0])
    assert supplied[0]['passages'][0]['text'] == evidence.passages[0].text
    assert validate_verdict(raw, [evidence]).evidence_quotes[0].wording_verified
