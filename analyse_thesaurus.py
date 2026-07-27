"""Mini-analyse: hoeveel PZH-keywords dragen een thesaurus-concept-URI
(gmx:Anchor/@xlink:href) versus vrije-tekst? Bepaalt of de SKOS-verrijking
80% of 20% van je concepten raakt -> of die tweede pass de moeite is.

Twee modi:
  python analyse_thesaurus.py --xmldir pzh_out/bronze_xml   # analyseer bestaande harvest
  python analyse_thesaurus.py --limit 100                    # harvest zelf een sample uit het Open Data Portaal

Classificatie per keyword:
  uri        -> gmx:Anchor met xlink:href  (koppelbaar op URI, goud)
  in-thes    -> gco:CharacterString maar staat in een MD_Keywords mét thesaurusName
                (koppelbaar op label binnen die thesaurus, fuzzy)
  free-text  -> geen thesaurus, geen URI (eilandje, niet koppelbaar)
"""
import argparse, json, glob
from collections import Counter
from lxml import etree

NS = {"gmd":"http://www.isotc211.org/2005/gmd",
      "gco":"http://www.isotc211.org/2005/gco",
      "gmx":"http://www.isotc211.org/2005/gmx",
      "xlink":"http://www.w3.org/1999/xlink"}

def _text(el):
    cs = el.find("gco:CharacterString", NS)
    an = el.find("gmx:Anchor", NS)
    if an is not None: return (an.text or "").strip(), an.get("{%s}href" % NS["xlink"])
    if cs is not None: return (cs.text or "").strip(), None
    return (el.text or "").strip(), None

def analyse_record(xml_bytes):
    """-> dict with 'keywords': [(label, thesaurus, kind), ...] and 'topics': [str, ...]."""
    root = etree.fromstring(xml_bytes)
    kws = []
    for mk in root.iterfind(".//gmd:MD_Keywords", NS):
        # thesaurusnaam (titel) van dit keyword-blok
        thes = ""
        tt = mk.find(".//gmd:thesaurusName//gmd:title", NS)
        if tt is not None:
            thes = _text(tt)[0]
        has_thes = bool(thes) or mk.find(".//gmd:thesaurusName", NS) is not None
        for kw in mk.iterfind("gmd:keyword", NS):
            label, uri = _text(kw)
            if not label and not uri: continue
            kind = "uri" if uri else ("in-thes" if has_thes else "free-text")
            kws.append((label, thes, kind))
    topics = [el.text.strip()
              for el in root.iterfind(".//gmd:topicCategory/gmd:MD_TopicCategoryCode", NS)
              if el.text and el.text.strip()]
    return {"keywords": kws, "topics": topics}

def report(per_record):
    kinds = Counter()
    thesauri = Counter()
    topic_counts = Counter()
    ds_with_uri = ds_with_any_thes = ds_with_topic = ds_total = 0
    for rec in per_record:
        kws, topics = rec["keywords"], rec["topics"]
        ds_total += 1
        rk = Counter(k for _,_,k in kws)
        kinds.update(rk)
        if rk["uri"]: ds_with_uri += 1
        if rk["uri"] or rk["in-thes"]: ds_with_any_thes += 1
        if topics: ds_with_topic += 1
        topic_counts.update(topics)
        for label, thes, kind in kws:
            if kind != "free-text" and thes: thesauri[thes] += 1
    tot_kw = sum(kinds.values()) or 1
    print("\n=== THESAURUS-DEKKING PZH ===")
    print(f"datasets geanalyseerd : {ds_total}")
    print(f"keywords totaal       : {tot_kw}")
    for k in ("uri","in-thes","free-text"):
        print(f"  {k:10s}: {kinds[k]:6d}  ({100*kinds[k]/tot_kw:4.1f}%)")
    print(f"\ndatasets met >=1 URI-keyword       : {ds_with_uri}/{ds_total} ({100*ds_with_uri/max(ds_total,1):.0f}%)")
    print(f"datasets met >=1 thesaurus-keyword : {ds_with_any_thes}/{ds_total} ({100*ds_with_any_thes/max(ds_total,1):.0f}%)")
    print("\ntop thesauri (waar keywords vandaan komen):")
    for name, n in thesauri.most_common(12):
        print(f"  {n:5d}  {name}")
    print(f"\n=== ISO TOPIC CATEGORIES ===")
    print(f"datasets met >=1 topicCategory : {ds_with_topic}/{ds_total} ({100*ds_with_topic/max(ds_total,1):.0f}%)")
    print("verdeling:")
    for cat, n in topic_counts.most_common():
        print(f"  {n:5d}  {cat}")
    print("\n>> Vuistregel: veel 'uri' -> SKOS-verrijking op URI loont direct.")
    print(">>            veel 'in-thes' -> loont via label-matching (fuzzy).")
    print(">>            veel 'free-text' -> eerst tagging verbeteren, dan pas SKOS.")
    print(">>            hoge topicCategory-dekking -> ISO-categorie-nodes bruikbaar als backbone.")
    return {"kinds":dict(kinds), "datasets":ds_total,
            "ds_with_uri":ds_with_uri, "ds_with_any_thes":ds_with_any_thes,
            "thesauri":dict(thesauri),
            "ds_with_topic":ds_with_topic, "topic_counts":dict(topic_counts)}

def from_xmldir(xmldir):
    files = glob.glob(f"{xmldir}/*.xml")
    print(f"Analyseer {len(files)} XML-bestanden uit {xmldir}")
    records = []
    for f in files:
        inhoud = open(f, "rb").read()
        if inhoud.strip():
            records.append(analyse_record(inhoud))
    return records

def from_odp(limit, page):
    from owslib.csw import CatalogueServiceWeb
    import requests, urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _orig = requests.Session.send
    requests.Session.send = lambda self, req, **kw: _orig(self, req, verify=False, **{k:v for k,v in kw.items() if k!="verify"})
    URL="https://opendata.Zuid-Holland.nl/geonetwork/srv/dut/csw"
    ISO="http://www.isotc211.org/2005/gmd"
    csw=CatalogueServiceWeb(URL, timeout=120)
    per, pos=[], 1
    while True:
        csw.getrecords2(constraints=[], outputschema=ISO, esn="full",
                        startposition=pos, maxrecords=page)
        recs=list(csw.records.values())
        if not recs: break
        for r in recs:
            xml=getattr(r,"xml",None)
            if xml: per.append(analyse_record(xml if isinstance(xml,bytes) else xml.encode()))
        print(f"  {len(per)} …")
        if limit and len(per)>=limit: return per[:limit]
        pos+=page
        if pos>csw.results.get("matches",0): break
    return per

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--xmldir")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--page", type=int, default=50)
    a=ap.parse_args()
    per = from_xmldir(a.xmldir) if a.xmldir else from_odp(a.limit, a.page)
    res = report(per)
    json.dump(res, open("analyse_thesaurus.json","w"), ensure_ascii=False, indent=2)
    print("\n-> analyse_thesaurus.json weggeschreven")
