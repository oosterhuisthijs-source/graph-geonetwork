"""Bouw een bipartiet NetworkX-graph: Dataset-knopen ↔ ISO-onderwerpscategorie-knopen.
Schrijft ook een gewogen dataset→dataset projectie weg.

Knopen:
  bipartite=0  dataset   — geïdentificeerd door gmd:fileIdentifier UUID
  bipartite=1  topic     — ISO MD_TopicCategoryCode string

Kanten bipartiet: dataset —— topic  (dataset valt onder deze onderwerpscategorie)
Kanten projectie: dataset —— dataset, gewicht = aantal gedeelde topics

Bron: Open Data Portaal Zuid-Holland (opendata.Zuid-Holland.nl/geonetwork/srv/dut/csw).
      Bevat ~2283 records, waarvan ~1400 bereikbaar als ISO 19139 GMD-dataset.
      iso19110-records (feature catalogues) worden als XML-commentaar teruggegeven
      en automatisch overgeslagen — geen page-level fouten zoals op NGR.

Cache: XML-bestanden worden weggeschreven naar --xmldir (standaard bronze_xml/).
       Bij een volgende run worden die hergebruikt zonder het portaal te bevragen.

Gebruik:
  python build_graph.py                        # harvest 100 records, sla op in bronze_xml/
  python build_graph.py --limit 0              # harvest alle PZH-records (~1400)
  python build_graph.py --xmldir bronze_xml/   # gebruik bestaande cache, sla netwerk over
"""
import argparse, glob, os
import networkx as nx
from networkx.algorithms import bipartite
from lxml import etree


TOPIC_NL = {
    "environment":                    "natuur en milieu",
    "boundaries":                     "grenzen",
    "society":                        "maatschappij",
    "planningCadastre":               "planning kadaster",
    "economy":                        "economie",
    "transportation":                 "transport",
    "biota":                          "biota",
    "inlandWaters":                   "binnenwater",
    "utilitiesCommunication":         "nutsbedrijven communicatie",
    "geoscientificInformation":       "geo wetenschappelijke data",
    "structure":                      "(civiele) structuren",
    "climatologyMeteorologyAtmosphere": "klimatologie, meteorologie atmosfeer",
    "location":                       "locatie",
    "imageryBaseMapsEarthCover":      "referentie materiaal aardbedekking",
    "farming":                        "landbouw en veeteelt",
    "health":                         "gezondheid",
    "elevation":                      "hoogte",
    "oceans":                         "oceanen",
    "intelligenceMilitary":           "defensie en inlichtingen",
}

NS = {
    "gmd": "http://www.isotc211.org/2005/gmd",
    "gco": "http://www.isotc211.org/2005/gco",
}

def _cs(root, xpath):
    """Geeft tekst van eerste gco:CharacterString op xpath, of ''."""
    el = root.find(xpath, NS)
    return (el.text or "").strip() if el is not None else ""

def extract(xml_bytes):
    """-> dict met uuid, titel, samenvatting, gewijzigd, topics, trefwoorden uit ruwe ISO-19115 XML."""
    root = etree.fromstring(xml_bytes)
    fid = root.find(".//gmd:fileIdentifier/gco:CharacterString", NS)
    uuid = fid.text.strip() if fid is not None and fid.text else None
    topics = [
        el.text.strip()
        for el in root.iterfind(".//gmd:topicCategory/gmd:MD_TopicCategoryCode", NS)
        if el.text and el.text.strip()
    ]
    trefwoorden = []
    for mk in root.iterfind(".//gmd:MD_Keywords", NS):
        tt = mk.find(".//gmd:thesaurusName//gmd:title/gco:CharacterString", NS)
        if tt is None or "Interprovinciale" not in (tt.text or ""):
            continue
        for kw in mk.iterfind("gmd:keyword", NS):
            cs = kw.find("gco:CharacterString", NS)
            if cs is not None and cs.text and cs.text.strip():
                trefwoorden.append(cs.text.strip())
    return {
        "uuid":         uuid,
        "titel":        _cs(root, ".//gmd:identificationInfo//gmd:citation//gmd:title/gco:CharacterString"),
        "samenvatting": _cs(root, ".//gmd:identificationInfo//gmd:abstract/gco:CharacterString"),
        "gewijzigd":    _cs(root, ".//gmd:dateStamp/gco:DateTime") or _cs(root, ".//gmd:dateStamp/gco:Date"),
        "topics":       topics,
        "trefwoorden":  trefwoorden,
    }

def laad_cache(xmldir):
    """Laad alle XML-bestanden uit de cache en geef lijst van record-dicts terug."""
    bestanden = glob.glob(f"{xmldir}/*.xml")
    print(f"Cache gevonden: {len(bestanden)} XML-bestanden in {xmldir}/")
    records = []
    for pad in bestanden:
        with open(pad, "rb") as f:
            inhoud = f.read()
        if inhoud.strip():
            records.append(extract(inhoud))
    return records

def harvest(limit, page, xmldir):
    """Harvest van het Open Data Portaal Zuid-Holland, sla elk record op als {uuid}.xml.

    Hervat automatisch: al gecachede UUIDs worden overgeslagen.
    iso19110-records (feature catalogues) worden door het portaal als XML-commentaar
    teruggegeven en stilzwijgend overgeslagen — geen page-level fouten.
    """
    from owslib.csw import CatalogueServiceWeb
    from owslib.ows import ExceptionReport
    import requests, urllib3
    # Bedrijfs-CA niet in Python-certbundle → sla SSL-verificatie over (intern verkeer)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _orig_send = requests.Session.send
    requests.Session.send = lambda self, req, **kw: _orig_send(self, req, verify=False, **{k:v for k,v in kw.items() if k!="verify"})
    URL = "https://opendata.Zuid-Holland.nl/geonetwork/srv/dut/csw"
    ISO = "http://www.isotc211.org/2005/gmd"
    os.makedirs(xmldir, exist_ok=True)

    # bepaal welke UUIDs al gecached zijn
    gecached = {os.path.splitext(os.path.basename(p))[0]
                for p in glob.glob(f"{xmldir}/*.xml")}
    if gecached:
        print(f"Cache: {len(gecached)} bestaande bestanden gevonden, worden overgeslagen")

    csw = CatalogueServiceWeb(URL, timeout=120)
    records, pos = [], 1
    totaal = None
    while True:
        try:
            csw.getrecords2(
                constraints=[],
                outputschema=ISO, esn="full", startposition=pos, maxrecords=page,
            )
        except ExceptionReport as e:
            print(f"  [!] pagina {pos}–{pos+page-1} overgeslagen: {e}")
            pos += page
            if totaal and pos > totaal:
                break
            continue

        if totaal is None:
            totaal = csw.results.get("matches", "?")
            print(f"Totaal beschikbare records op Open Data Portaal: {totaal}")

        recs = list(csw.records.values())
        if not recs:
            break
        for r in recs:
            xml = getattr(r, "xml", None)
            if not xml:
                continue
            xml_bytes = xml if isinstance(xml, bytes) else xml.encode()
            rec = extract(xml_bytes)
            uuid = rec["uuid"] or f"onbekend_{pos}_{len(records)}"
            if uuid in gecached:
                continue  # al in cache, overslaan
            records.append(rec)
            gecached.add(uuid)
            with open(os.path.join(xmldir, f"{uuid}.xml"), "wb") as f:
                f.write(xml_bytes)

        print(f"  {len(gecached)}/{totaal} gecached, {len(records)} nieuw deze run …")
        if limit and len(records) >= limit:
            return records[:limit]
        pos += page
        if isinstance(totaal, int) and pos > totaal:
            break

    return records

def bouw_graph(records):
    """Bouw graph van records met drie knooptypes: topic, trefwoord, dataset. -> nx.Graph"""
    G = nx.Graph()
    overgeslagen = 0
    for rec in records:
        uuid = rec["uuid"]
        if not uuid:
            overgeslagen += 1
            continue
        G.add_node(uuid, bipartite=0, node_type="dataset",
                   label=rec["titel"] or uuid,
                   titel=rec["titel"],
                   samenvatting=rec["samenvatting"],
                   gewijzigd=rec["gewijzigd"],
                   url_opendata=OPENDATA_URL.format(uuid=uuid))
        for topic in rec["topics"]:
            G.add_node(topic, bipartite=1, node_type="topic",
                       label=TOPIC_NL.get(topic, topic))
            G.add_edge(uuid, topic)
        for trefwoord in rec["trefwoorden"]:
            G.add_node(trefwoord, bipartite=2, node_type="trefwoord", label=trefwoord)
            G.add_edge(uuid, trefwoord)
    if overgeslagen:
        print(f"  ({overgeslagen} records overgeslagen wegens ontbrekende fileIdentifier)")
    return G

def voeg_hoofd_topic_toe(G):
    """Voeg 'hoofd_topic' attribuut toe aan elk dataset-knoop.

    Keuze: het naburige topic met de hoogste graad (meeste datasets).
    Bij gelijkspel wint het topic dat als eerste voorkomt.
    Datasets zonder topic krijgen hoofd_topic=''.
    """
    for n, d in G.nodes(data=True):
        if d["bipartite"] != 0:
            continue
        buren = [b for b in G.neighbors(n) if G.nodes[b]["bipartite"] == 1]
        if buren:
            hoofd = max(buren, key=lambda t: G.degree(t))
        else:
            hoofd = ""
        G.nodes[n]["hoofd_topic"] = TOPIC_NL.get(hoofd, hoofd)

def bouw_projectie(G):
    """Gewogen dataset-dataset projectie: gewicht = aantal gedeelde topics."""
    # Gebruik alleen datasets + topics zodat gedeelde trefwoorden niet meewegen.
    relevante_knopen = [n for n, d in G.nodes(data=True) if d["bipartite"] in (0, 1)]
    sub = G.subgraph(relevante_knopen)
    dataset_knopen = {n for n, d in sub.nodes(data=True) if d["bipartite"] == 0}
    P = bipartite.weighted_projected_graph(sub, dataset_knopen)
    for n in P.nodes():
        P.nodes[n].update(G.nodes[n])
    return P

OPENDATA_URL = "https://opendata.zuid-holland.nl/geonetwork/srv/dut/catalog.search#/metadata/{uuid}"

def exporteer_html(graphml_pad, html_pad):
    """Genereer interactieve HTML-viewer via build_graph/build.py (force-graph bibliotheek)."""
    import importlib.util, sys as _sys
    build_pad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_graph", "build.py")
    spec = importlib.util.spec_from_file_location("build_graph_build", build_pad)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    nodes, links = mod.parse_graphml(graphml_pad)
    tpl  = open(os.path.join(os.path.dirname(build_pad), "template.html"), encoding="utf-8").read()
    html = (tpl
            .replace("/*__LIB__*/", mod.find_lib())
            .replace("/*__DATA__*/",
                     "const GRAPH = " + __import__("json").dumps(
                         {"nodes": nodes, "links": links}, ensure_ascii=False) + ";"))
    open(html_pad, "w", encoding="utf-8").write(html)

def bouw_topic_cooccurrentie(G):
    """Gewogen topic-topic projectie: gewicht = aantal datasets dat beide topics deelt."""
    # weighted_projected_graph volgt 2-hop paden en neemt alle buren mee, niet alleen
    # de knooppunten in de projectieset. Datasets zijn verbonden met zowel topics als
    # trefwoorden, waardoor trefwoorden in T lekken als we G direct gebruiken.
    # Oplossing: projecteer op een deelgraph met alleen datasets + topics.
    relevante_knopen = [n for n, d in G.nodes(data=True) if d["bipartite"] in (0, 1)]
    sub = G.subgraph(relevante_knopen)
    topic_knopen = {n for n, d in sub.nodes(data=True) if d["bipartite"] == 1}
    T = bipartite.weighted_projected_graph(sub, topic_knopen)
    return T

def rapport(G, P, T):
    datasets    = [n for n, d in G.nodes(data=True) if d.get("node_type") == "dataset"]
    topics      = [n for n, d in G.nodes(data=True) if d.get("node_type") == "topic"]
    trefwoorden = [n for n, d in G.nodes(data=True) if d.get("node_type") == "trefwoord"]
    print(f"\n=== GRAPH ===")
    print(f"dataset-knopen    : {len(datasets)}")
    print(f"topic-knopen      : {len(topics)}")
    print(f"trefwoord-knopen  : {len(trefwoorden)}")
    print(f"kanten         : {G.number_of_edges()}")
    print(f"\ndatasets per topic (graad):")
    for t in sorted(topics, key=lambda t: G.degree(t), reverse=True):
        print(f"  {G.degree(t):4d}  {G.nodes[t].get('label', t)}")
    print(f"\n=== PROJECTIE (dataset-dataset) ===")
    print(f"knopen  : {P.number_of_nodes()}")
    print(f"kanten  : {P.number_of_edges()}")
    gewichten = [d["weight"] for _, _, d in P.edges(data=True)]
    if gewichten:
        print(f"gewicht : min={min(gewichten)}  max={max(gewichten)}  "
              f"gemiddeld={sum(gewichten)/len(gewichten):.1f}")
    print(f"\n=== TOPIC CO-OCCURRENTIE ===")
    print(f"knopen  : {T.number_of_nodes()}")
    print(f"kanten  : {T.number_of_edges()}")
    print(f"\nsterkste topic-combinaties (gewicht = gedeelde datasets):")
    kanten = sorted(T.edges(data=True), key=lambda e: e[2]["weight"], reverse=True)
    for u, v, d in kanten[:15]:
        lu = G.nodes[u].get('label', u) if u in G.nodes else u
        lv = G.nodes[v].get('label', v) if v in G.nodes else v
        print(f"  {d['weight']:4d}  {lu} — {lv}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",  type=int, default=100,
                    help="Max te harvesten records; 0 = alles")
    ap.add_argument("--page",   type=int, default=50)
    ap.add_argument("--xmldir", default="bronze_xml",
                    help="Cache-map voor ruwe XML; als aanwezig wordt netwerk overgeslagen")
    ap.add_argument("--out",    default="graph.graphml")
    ap.add_argument("--out-projectie",   default="graph_projectie.graphml")
    ap.add_argument("--out-topics",      default="graph_topics.graphml")
    ap.add_argument("--out-html",        default="graph.html")
    a = ap.parse_args()

    # bij --limit 0 altijd harvesten (pikt nieuwe records op, slaat gecachede over)
    # bij een positieve limit: gebruik cache als die er is
    if a.limit == 0 or not (os.path.isdir(a.xmldir) and glob.glob(f"{a.xmldir}/*.xml")):
        records = harvest(a.limit, a.page, a.xmldir)
        records = laad_cache(a.xmldir)
    else:
        records = laad_cache(a.xmldir)

    G = bouw_graph(records)
    voeg_hoofd_topic_toe(G)
    P = bouw_projectie(G)
    T = bouw_topic_cooccurrentie(G)
    rapport(G, P, T)

    nx.write_graphml(G, a.out)
    print(f"\n-> {a.out} weggeschreven")
    nx.write_graphml(P, a.out_projectie)
    print(f"-> {a.out_projectie} weggeschreven")
    nx.write_graphml(T, a.out_topics)
    print(f"-> {a.out_topics} weggeschreven")
    exporteer_html(a.out, a.out_html)
    print(f"-> {a.out_html} weggeschreven")
