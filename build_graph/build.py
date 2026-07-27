#!/usr/bin/env python3
"""
Bouwt een self-contained, Obsidian-achtige graph-viewer uit een GraphML-bestand.
Library (force-graph) en data worden IN de HTML gebakken -> werkt offline / achter firewall.

Gebruik:
    python build.py                              # graph.graphml -> graph-viewer.html
    python build.py data/mijn.graphml out.html   # eigen in-/output

Nodig in de repo:
    template.html            (de viewer-shell met /*__LIB__*/ en /*__DATA__*/ placeholders)
    vendor/force-graph.min.js (eenmalig committen: `npm pack force-graph` -> uitpakken)
"""
import sys, os, json, glob, subprocess, tempfile, tarfile
import xml.etree.ElementTree as ET

NS = {'g': 'http://graphml.graphdrawing.org/xmlns'}
HERE = os.path.dirname(os.path.abspath(__file__))


def find_lib():
    """Zoek de gevendorde force-graph bundle; val terug op `npm pack` indien afwezig."""
    for p in ('vendor/force-graph.min.js', 'force-graph.min.js',
              os.path.join(HERE, 'vendor/force-graph.min.js'),
              os.path.join(HERE, 'force-graph.min.js')):
        if os.path.exists(p):
            return open(p, encoding='utf-8').read()
    # fallback: haal via npm op (heb je internet + npm nodig)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['npm', 'pack', 'force-graph'], cwd=tmp, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tgz = glob.glob(os.path.join(tmp, 'force-graph-*.tgz'))[0]
            with tarfile.open(tgz) as t:
                t.extract('package/dist/force-graph.min.js', tmp)
            js = open(os.path.join(tmp, 'package/dist/force-graph.min.js'), encoding='utf-8').read()
        # cache 'm zodat de volgende build offline werkt
        os.makedirs('vendor', exist_ok=True)
        open('vendor/force-graph.min.js', 'w', encoding='utf-8').write(js)
        return js
    except Exception as e:
        sys.exit("force-graph.min.js niet gevonden en npm pack faalde.\n"
                 "Los op met:  npm pack force-graph  (pak dist/force-graph.min.js uit -> vendor/)\n"
                 f"Detail: {e}")


def parse_graphml(path):
    root = ET.parse(path).getroot()
    keys = {k.get('id'): k.get('attr.name') for k in root.findall('.//g:key', NS)}

    def data(el):
        d = {}
        for dd in el.findall('g:data', NS):
            d[keys.get(dd.get('key'), dd.get('key'))] = (dd.text or '').strip()
        return d

    graph = root.find('g:graph', NS)
    nodes, links, deg = [], [], {}
    for n in graph.findall('g:node', NS):
        nid = n.get('id'); d = data(n)
        nodes.append({
            'id':          nid,
            'label':       d.get('label') or d.get('titel') or nid,
            'type':        d.get('node_type', ''),
            'gew':         d.get('gewijzigd', ''),
            'sam':         d.get('samenvatting', ''),
            'url_opendata':d.get('url_opendata', ''),
        })
        deg[nid] = 0
    for e in graph.findall('g:edge', NS):
        s, t = e.get('source'), e.get('target')
        links.append({'source': s, 'target': t})
        deg[s] = deg.get(s, 0) + 1
        deg[t] = deg.get(t, 0) + 1
    for n in nodes:
        n['deg'] = deg.get(n['id'], 0)
    return nodes, links


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'graph.graphml'
    out = sys.argv[2] if len(sys.argv) > 2 else 'graph-viewer.html'
    tpl_path = os.path.join(HERE, 'template.html')
    if not os.path.exists(tpl_path):
        tpl_path = 'template.html'

    nodes, links = parse_graphml(src)
    tpl = open(tpl_path, encoding='utf-8').read()
    html = (tpl
            .replace('/*__LIB__*/', find_lib())
            .replace('/*__DATA__*/',
                     'const GRAPH = ' + json.dumps({'nodes': nodes, 'links': links},
                                                    ensure_ascii=False) + ';'))
    open(out, 'w', encoding='utf-8').write(html)
    print(f"OK  {len(nodes)} nodes, {len(links)} edges  ->  {out}  ({len(html):,} bytes)")


if __name__ == '__main__':
    main()
