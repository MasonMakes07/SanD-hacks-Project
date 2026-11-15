"""One public originating article and at most one relevant observed body link."""
from urllib.parse import urlsplit

from .models import Source
from .query import keywords
from .urls import UnsafeUrlError, dedup_key, is_social_url, normalize_url

_DOCUMENT_TERMS = {'study', 'report', 'paper', 'data', 'research', 'document', 'findings', 'records', 'trial'}
_NAV_TERMS = {'subscribe', 'login', 'signup', 'privacy', 'contact', 'advertise', 'sponsored', 'donate'}


def article_url(url):
    if not url:
        return None
    try:
        normalized = normalize_url(url)
    except (UnsafeUrlError, ValueError):
        return None
    host = (urlsplit(normalized).hostname or '').removeprefix('www.')
    if is_social_url(normalized) or host in {'youtube.com', 'm.youtube.com', 'youtu.be'}:
        return None
    return normalized


def new_id(sources):
    used = {s.id.lower() for s in sources}
    number = 1
    while f's{number}' in used:
        number += 1
    return f's{number}'


def page_keys(page):
    return {dedup_key(normalize_url(url)) for url in (page.requested_url, page.final_url)}


def select_body_link(claim, page):
    terms = set(keywords(claim))
    candidates = []
    for index, link in enumerate(page.parsed.links):
        url = article_url(link.url)
        if not url or dedup_key(url) in page_keys(page):
            continue
        anchor_terms = set(keywords(link.anchor_text))
        if anchor_terms & _NAV_TERMS:
            continue
        context_overlap = len(terms & set(keywords(link.context)))
        anchor_overlap = len(terms & anchor_terms)
        if context_overlap < min(2, max(1, len(terms))):
            continue
        if not anchor_overlap and not (anchor_terms & _DOCUMENT_TERMS):
            continue
        candidates.append((anchor_overlap * 2 + context_overlap, -index, link))
    return max(candidates, key=lambda row: row[:2])[2] if candidates else None


async def fetch_seed_context(claim, sources, fetcher, budget, seed_url):
    """Return sources, fetched pages, warnings and attempted normalized URL keys."""
    sources = list(sources)
    pages, warnings, attempted = [], [], set()
    url = article_url(seed_url)
    if url is None:
        return sources, pages, (['Originating page is not a usable public article URL.'] if seed_url else []), attempted
    seed = next((s for s in sources if dedup_key(s.url) == dedup_key(url)), None)
    candidate = seed or Source(id=new_id(sources), title='Originating article', url=url, date=None, excerpt='',
        provenance={'provider': 'originating_page', 'role': 'originating_article', 'originating_url': url})
    attempted.add(dedup_key(url))
    fetched, notes = await fetcher.fetch_pages([(candidate.id, url)], budget)
    warnings.extend(notes)
    if not fetched:
        return sources, pages, warnings, attempted
    if seed is None:
        sources.append(candidate)
    else:
        sources = [s.model_copy(update={'provenance': {**s.provenance, 'role': 'originating_article', 'originating_url': url}})
                   if s.id == seed.id else s for s in sources]
    pages.extend(fetched)
    attempted.update(page_keys(fetched[0]))
    link = select_body_link(claim, fetched[0])
    if link is None or budget.remaining <= 0:
        return sources, pages, warnings, attempted
    target = next((s for s in sources if dedup_key(s.url) == dedup_key(link.url)), None)
    candidate = target or Source(id=new_id(sources), title=link.anchor_text[:300] or 'Linked document',
        url=link.url, date=None, excerpt='', provenance={'provider': 'observed_body_link',
        'citing_source_id': fetched[0].source_id, 'observed_link': link.url, 'anchor_text': link.anchor_text[:300]})
    if dedup_key(link.url) in attempted:
        return sources, pages, warnings, attempted
    attempted.add(dedup_key(link.url))
    linked, notes = await fetcher.fetch_pages([(candidate.id, link.url)], budget)
    warnings.extend(notes)
    if linked:
        if target is None:
            sources.append(candidate)
        pages.extend(linked)
        attempted.update(page_keys(linked[0]))
    return sources, pages, warnings, attempted


def deduplicate_documents(sources, pages):
    """Observed request/final URL aliases coalesce; retain the first source ID.

    A publisher-declared canonical URL alone does not merge documents.
    """
    by_id = {p.source_id: p for p in pages}
    keys_by_id = {s.id: {dedup_key(s.url)} | (page_keys(by_id[s.id]) if s.id in by_id else set()) for s in sources}
    groups = []
    for source in sources:
        keys = keys_by_id[source.id]
        overlapping = [group for group in groups if group[1] & keys]
        if not overlapping:
            groups.append(([source], set(keys)))
        else:
            first = overlapping[0]
            first[0].append(source); first[1].update(keys)
            for other in overlapping[1:]:
                first[0].extend(other[0]); first[1].update(other[1]); groups.remove(other)
    kept, mapping = [], {}
    for members, keys in groups:
        first = members[0]
        if any(s.provenance.get('role') == 'originating_article' for s in members):
            origin = next(s for s in members if s.provenance.get('role') == 'originating_article')
            first = first.model_copy(update={'provenance': {**first.provenance, 'role': 'originating_article', 'originating_url': origin.provenance.get('originating_url')}})
        kept.append(first)
        mapping.update({s.id: first.id for s in members})
    selected_pages = {}
    for page in pages:
        canonical_id = mapping[page.source_id]
        if canonical_id not in selected_pages:
            from dataclasses import replace
            selected_pages[canonical_id] = replace(page, source_id=canonical_id)
    return kept, list(selected_pages.values())
