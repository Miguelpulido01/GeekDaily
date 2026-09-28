import json, os, re, html, urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

KEY = os.environ.get("GEMINI_API_KEY", "")
MODELOS = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-2.5-flash", "gemini-2.5-flash-lite"] if m]
UA = "Mozilla/5.0 (compatible; GeekDailyBot/0.1)"
CFG = json.load(open("feeds.json", encoding="utf-8"))
AHORA = datetime.now(timezone.utc)
LIMITE = AHORA - timedelta(hours=48)
ORIGENES = ("filtracion", "pista", "fans")


def loc(tag):
    return tag.rsplit("}", 1)[-1]


def texto(e):
    return "".join(e.itertext()).strip()


def limpio(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def fecha(s):
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def leer(f):
    req = urllib.request.Request(f["u"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raiz = ET.fromstring(r.read())
    salida = []
    for it in raiz.iter():
        if loc(it.tag) not in ("item", "entry"):
            continue
        d, imgs = {}, []
        for c in it:
            k = loc(c.tag)
            if k == "title":
                d["ti"] = limpio(texto(c))
            elif k == "link":
                d["u"] = d.get("u") or c.get("href") or texto(c)
            elif k in ("description", "summary", "content", "encoded"):
                raw = texto(c)
                d["d"] = d.get("d") or limpio(raw)[:400]
                imgs += re.findall(r'<img[^>]+src=["\']([^"\']+)', html.unescape(raw))
            elif k in ("pubDate", "published", "updated", "date"):
                d["p"] = d.get("p") or fecha(texto(c))
        for c in it.iter():
            k, u = loc(c.tag), c.get("url") or ""
            if k in ("content", "thumbnail") and u.startswith("http") and not c.get("type", "image").startswith(("video", "audio")):
                imgs.append(u)
            elif k == "enclosure" and c.get("type", "").startswith("image") and u.startswith("http"):
                imgs.append(u)
        if not (d.get("ti") and d.get("u") and d.get("p")) or d["p"] < LIMITE:
            continue
        d["img"] = list(dict.fromkeys(i for i in imgs if i.startswith("http")))
        d.setdefault("d", "")
        salida.append(d)
    return salida


def reunir():
    arts = []
    for f in CFG["fuentes"]:
        try:
            lista = leer(f)
        except Exception as e:
            print("FALLÓ", f["n"], e)
            continue
        lista.sort(key=lambda a: a["p"], reverse=True)
        for a in lista[:8]:
            a.update(n=f["n"], l=f["l"], foro=f.get("foro", False), oficial=f.get("oficial", False))
            arts.append(a)
        print("ok", f["n"], len(lista[:8]))
    arts.sort(key=lambda a: a["p"], reverse=True)
    return arts[:70]


PROMPT = """Eres el editor de Geek Daily, una app de noticias geek sin clickbait, sin introducciones y sin relleno.
Recibes una lista numerada de artículos recientes (fuente, idioma, título, resumen). Agrupa los que hablan del mismo hecho y devuelve SOLO un arreglo JSON.
Cada elemento: {"tipo":"noticia|rumor|falso","origen":"filtracion|pista|fans","titulo":"...","descripcion":"...","tags":[...],"fuentes":[números]}
Reglas:
- Escribe en español, con tus propias palabras. Título neutro y directo, sin anzuelos. Descripción: 1 o 2 frases con datos concretos (qué, cuándo, cuánto, plataformas), sin introducciones.
- Usa SOLO información presente en los textos. Si un dato no aparece, no lo menciones ni lo inventes.
- No traduzcas nombres de juegos, estudios, personajes ni productos.
- "noticia": hecho confirmado por el propio estudio o empresa, o por varias fuentes distintas. Con una sola fuente es "rumor".
- "rumor": filtraciones, pistas o especulación. Redáctalo en condicional ("según X, podría...") y nunca como un hecho.
- "falso": solo si los textos dicen de forma explícita que algo fue desmentido o es un bulo.
- "origen" solo aplica a rumores: filtracion (filtraciones), pista (pistas de desarrolladores o creadores), fans (análisis o teorías de fans).
- Los foros (marcados "foro") nunca bastan por sí solos para confirmar algo.
- "tags": hasta 3, elegidos SOLO de esta lista: __TEMAS__.
- Ignora lo que no le interese a un geek (política, deportes, ofertas genéricas, opinión).
- "fuentes": los números de los artículos que respaldan el elemento.
"""


def gemini(prompt):
    err = None
    for m in MODELOS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
            cuerpo = {"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
            req = urllib.request.Request(url, json.dumps(cuerpo).encode(),
                                         {"Content-Type": "application/json", "x-goog-api-key": KEY})
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            t = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            print("modelo usado:", m)
            return json.loads(t)
        except Exception as e:
            err = e
            print("modelo falló:", m, e)
    raise SystemExit(f"Gemini no respondió: {err}")


def main():
    if not KEY:
        raise SystemExit("Falta GEMINI_API_KEY")
    arts = reunir()
    if len(arts) < 5:
        raise SystemExit("Muy pocos artículos; no se toca news.json")
    lineas = [f"[{i}] ({a['n']}, {a['l']}{', foro' if a['foro'] else ''}) {a['ti']} — {a['d'][:250]}"
              for i, a in enumerate(arts)]
    res = gemini(PROMPT.replace("__TEMAS__", ", ".join(CFG["temas"])) + "\nARTÍCULOS:\n" + "\n".join(lineas))
    if not isinstance(res, list):
        raise SystemExit("Formato inesperado de Gemini")
    fuera = []
    for g in res:
        try:
            idx = [i for i in g.get("fuentes", []) if isinstance(i, int) and 0 <= i < len(arts)]
            S = list({arts[i]["n"]: arts[i] for i in reversed(idx)}.values())
            if not S or not g.get("titulo") or not g.get("descripcion"):
                continue
            tipo = g.get("tipo") if g.get("tipo") in ("noticia", "rumor", "falso") else "rumor"
            firmes = {a["n"] for a in S if not a["foro"]}
            if tipo in ("noticia", "falso") and len(firmes) < 2 and not any(a["oficial"] for a in S):
                print("descartado (pocas fuentes firmes):", g["titulo"])
                continue
            item = {"t": tipo, "p": max(a["p"] for a in S).isoformat(),
                    "ti": g["titulo"][:140], "d": g["descripcion"][:300],
                    "img": list(dict.fromkeys(i for a in S for i in a["img"]))[:2],
                    "tags": [t for t in g.get("tags", []) if t in CFG["temas"]][:3],
                    "f": [{"n": a["n"], "l": a["l"], "u": a["u"]} for a in S]}
            if tipo == "rumor":
                item["o"] = g.get("origen") if g.get("origen") in ORIGENES else "filtracion"
            fuera.append(item)
        except Exception as e:
            print("elemento omitido:", e)
    if not fuera:
        raise SystemExit("Sin resultados válidos; no se toca news.json")
    fuera.sort(key=lambda x: x["p"], reverse=True)
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(fuera[:30], f, ensure_ascii=False, indent=1)
    print("news.json actualizado con", len(fuera[:30]), "tarjetas")


main()
