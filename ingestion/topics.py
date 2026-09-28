"""Topic taxonomy used for interest matching.

A course gets a topic only when one of the topic's keywords occurs in its own text
(title, bulletin description, handout description/syllabus). The matched keyword and
the field it came from are stored so every "matches your interest" claim is explainable.
"""
from __future__ import annotations

import re

TAXONOMY: dict[str, dict] = {
    "artificial_intelligence": {"label": "Artificial intelligence", "aliases": ["ai", "a.i.", "artificial intelligence"],
        "keywords": ["artificial intelligence", "machine learning", "deep learning", "neural network", "neural networks",
                     "natural language processing", "computer vision", "reinforcement learning", "generative",
                     "large language model", "llm", "intelligent agent", "intelligent systems", "knowledge representation",
                     "pattern recognition", "fuzzy logic", "expert system", "genetic algorithm", "computational learning",
                     "bio-inspired intelligence", "transformer", "chatbot", "robot learning", "ai "]},
    "machine_learning": {"label": "Machine learning", "aliases": ["ml", "machine learning"],
        "keywords": ["machine learning", "deep learning", "neural network", "supervised", "unsupervised", "classification",
                     "regression", "clustering", "reinforcement learning", "learning theory", "support vector",
                     "random forest", "gradient descent", "feature selection"]},
    "data_science": {"label": "Data science & analytics", "aliases": ["data science", "analytics", "big data", "data"],
        "keywords": ["data science", "data mining", "analytics", "big data", "data analysis", "visualization",
                     "statistical learning", "information retrieval", "recommender", "graph mining", "data warehouse",
                     "predictive"]},
    "security": {"label": "Security & cryptography", "aliases": ["security", "cybersecurity", "cyber security", "crypto"],
        "keywords": ["security", "cryptography", "cryptographic", "encryption", "forensics", "malware", "intrusion",
                     "blockchain", "privacy", "authentication"]},
    "networks": {"label": "Computer networks", "aliases": ["networking", "networks", "network"],
        "keywords": ["computer network", "networking", "tcp/ip", "internet", "routing", "wireless network",
                     "network programming", "internetworking", "protocol"]},
    "systems": {"label": "Computer systems", "aliases": ["systems", "operating systems", "os", "distributed systems"],
        "keywords": ["operating system", "distributed", "parallel computing", "cloud computing", "compiler",
                     "computer architecture", "embedded software", "real time systems", "storage", "virtualization"]},
    "software_engineering": {"label": "Software engineering", "aliases": ["software", "software engineering", "programming"],
        "keywords": ["software engineering", "object oriented", "software development", "software architecture",
                     "design patterns", "testing", "programming", "mobile app", "web development"]},
    "algorithms_theory": {"label": "Algorithms & theory", "aliases": ["algorithms", "theory", "theoretical cs"],
        "keywords": ["algorithm", "complexity", "computability", "automata", "graph theory", "combinatorial",
                     "optimization", "approximation", "computational geometry", "discrete mathematics"]},
    "quantum": {"label": "Quantum technologies", "aliases": ["quantum"],
        "keywords": ["quantum"]},
    "robotics": {"label": "Robotics & automation", "aliases": ["robotics", "robots", "automation"],
        "keywords": ["robot", "robotics", "automation", "mechatronics", "autonomous", "manipulator", "drone"]},
    "electronics_vlsi": {"label": "Electronics & VLSI", "aliases": ["vlsi", "electronics", "chip design", "semiconductor"],
        "keywords": ["vlsi", "digital design", "analog", "semiconductor", "integrated circuit", "microelectronics",
                     "fpga", "embedded", "microprocessor", "device", "nanoelectronic"]},
    "signal_processing": {"label": "Signal & image processing", "aliases": ["signal processing", "dsp", "image processing"],
        "keywords": ["signal processing", "image processing", "filter design", "speech", "audio", "wavelet"]},
    "communication": {"label": "Communication engineering", "aliases": ["communication", "telecom", "5g"],
        "keywords": ["communication system", "wireless", "antenna", "modulation", "optical communication", "5g",
                     "information theory", "rf "]},
    "power_energy": {"label": "Power & energy", "aliases": ["energy", "power systems", "renewable", "ev"],
        "keywords": ["power system", "renewable", "energy", "solar", "battery", "electric vehicle", "power electronics",
                     "smart grid", "fuel cell"]},
    "finance": {"label": "Finance", "aliases": ["finance", "fintech", "investment", "banking"],
        "keywords": ["finance", "financial", "investment", "portfolio", "derivatives", "banking", "stock", "valuation",
                     "fintech", "risk management", "accounting"]},
    "economics": {"label": "Economics", "aliases": ["economics", "econ"],
        "keywords": ["economics", "economic", "microeconomics", "macroeconomics", "econometrics", "game theory", "market"]},
    "management": {"label": "Management & business", "aliases": ["management", "business", "marketing", "mba"],
        "keywords": ["management", "marketing", "business", "organizational", "strategy", "supply chain", "operations",
                     "human resource", "leadership", "consulting"]},
    "entrepreneurship": {"label": "Entrepreneurship", "aliases": ["startup", "entrepreneurship", "venture"],
        "keywords": ["entrepreneur", "venture", "startup", "innovation", "business plan"]},
    "psychology": {"label": "Psychology", "aliases": ["psychology"],
        "keywords": ["psychology", "cognitive", "behaviour", "behavior", "mental health", "metacognition"]},
    "philosophy_ethics": {"label": "Philosophy & ethics", "aliases": ["philosophy", "ethics"],
        "keywords": ["philosophy", "ethics", "ethical", "logic", "moral", "aesthetics"]},
    "literature_languages": {"label": "Literature & languages", "aliases": ["literature", "english", "languages", "writing"],
        "keywords": ["literature", "fiction", "poetry", "drama", "linguistics", "english", "language", "writing",
                     "novel", "phonetics"]},
    "history_society": {"label": "History, politics & society", "aliases": ["history", "politics", "sociology", "society"],
        "keywords": ["history", "political", "politics", "society", "social", "gender", "globalization", "development",
                     "public policy", "anthropology", "international relations", "sociology"]},
    "media_arts": {"label": "Media, film & arts", "aliases": ["film", "media", "arts", "music", "design", "photography"],
        "keywords": ["film", "cinema", "media", "journalism", "music", "art", "theatre", "photography", "advertis",
                     "video", "visual culture", "comics", "creative"]},
    "environment": {"label": "Environment & sustainability", "aliases": ["environment", "sustainability", "climate"],
        "keywords": ["environment", "sustainab", "climate", "pollution", "water", "waste", "ecology", "green"]},
    "biology": {"label": "Biology & biotechnology", "aliases": ["biology", "biotech", "bio", "genetics"],
        "keywords": ["biology", "biological", "genetic", "genomics", "cell", "microbio", "biotechnology", "protein",
                     "molecular biology", "immunology", "bioinformatics", "neuroscience"]},
    "healthcare": {"label": "Healthcare & biomedical", "aliases": ["healthcare", "medical", "biomedical", "health"],
        "keywords": ["health", "medical", "biomedical", "clinical", "drug", "pharma", "disease", "hospital"]},
    "chemistry": {"label": "Chemistry", "aliases": ["chemistry"],
        "keywords": ["chemistry", "chemical", "organic", "inorganic", "spectroscopy", "catalysis", "polymer"]},
    "materials_nano": {"label": "Materials & nanoscience", "aliases": ["materials", "nano", "nanotechnology"],
        "keywords": ["material", "nano", "composite", "thin film", "metallurgy", "alloy", "ceramic"]},
    "mechanics_structures": {"label": "Mechanics & structures", "aliases": ["structures", "mechanics", "civil"],
        "keywords": ["mechanics", "structural", "structure", "solid", "vibration", "finite element", "concrete",
                     "steel", "geotechnical", "bridge", "earthquake"]},
    "thermal_fluids": {"label": "Thermal & fluids", "aliases": ["thermodynamics", "fluids", "heat transfer", "cfd"],
        "keywords": ["thermodynamic", "fluid", "heat transfer", "combustion", "cfd", "turbomachin", "aerodynamic",
                     "propulsion", "refrigeration"]},
    "manufacturing": {"label": "Manufacturing & production", "aliases": ["manufacturing", "production"],
        "keywords": ["manufacturing", "production", "machining", "additive", "casting", "welding", "lean", "quality"]},
    "mathematics": {"label": "Mathematics", "aliases": ["math", "maths", "mathematics"],
        "keywords": ["mathematics", "algebra", "calculus", "differential equation", "topology", "number theory",
                     "numerical", "real analysis", "complex analysis", "measure theory"]},
    "statistics": {"label": "Statistics & probability", "aliases": ["statistics", "probability", "stats"],
        "keywords": ["statistic", "probability", "stochastic", "regression", "bayesian", "sampling", "time series"]},
    "physics": {"label": "Physics", "aliases": ["physics"],
        "keywords": ["physics", "quantum mechanics", "electromagnetic", "optics", "relativity", "astrophysics",
                     "solid state", "particle", "cosmology"]},
    "space_aero": {"label": "Aerospace & space", "aliases": ["aerospace", "space", "aeronautics", "flight"],
        "keywords": ["aircraft", "flight", "aerospace", "space", "satellite", "rocket", "aeronautic", "orbital"]},
    "hci_design": {"label": "Design & HCI", "aliases": ["hci", "ux", "design thinking", "user experience"],
        "keywords": ["human-computer", "human computer", "user experience", "interaction design", "design thinking",
                     "usability", "product design"]},
}


def _kw_regex(kw: str) -> re.Pattern:
    kw = kw.strip()
    if len(kw) <= 3:
        return re.compile(rf"(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])", re.I)
    return re.compile(rf"(?<![A-Za-z]){re.escape(kw)}", re.I)


_COMPILED = {t: [(k, _kw_regex(k)) for k in v["keywords"]] for t, v in TAXONOMY.items()}


def tag_topics(fields: dict[str, str]) -> dict[str, dict]:
    """fields: {"title": ..., "bulletin_description": ..., "handout": ...} -> topic -> hit detail."""
    out: dict[str, dict] = {}
    for topic, kws in _COMPILED.items():
        hits = []
        score = 0.0
        for fname, text in fields.items():
            if not text:
                continue
            for kw, rx in kws:
                n = len(rx.findall(text))
                if n:
                    w = 3.0 if fname == "title" else (1.5 if fname == "bulletin_description" else 1.0)
                    score += w * min(n, 3)
                    hits.append({"keyword": kw, "field": fname, "count": n})
        if hits and score >= 1.5:
            out[topic] = {"score": round(score, 2), "hits": hits[:6]}
    return out


def resolve_interest(term: str) -> list[str]:
    """Map a free-text interest to taxonomy topics via labels/aliases (deterministic)."""
    t = term.strip().lower()
    found = []
    for topic, v in TAXONOMY.items():
        names = [v["label"].lower(), topic.replace("_", " ")] + [a.lower() for a in v["aliases"]]
        if any(t == n or (len(t) > 3 and (t in n or n in t)) for n in names):
            found.append(topic)
    return found
