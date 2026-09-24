import os

import numpy as np
import requests
from flask import jsonify
from openai import OpenAI

from app import saved_recipes_collection, user_preferences_collection


SPOONACULAR_API_KEY = os.getenv("API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = os.getenv(
    "RECOMMENDATION_EMBEDDING_MODEL",
    "text-embedding-ada-002",
)


def get_embedding(text):
    """Return an embedding for text, or None when embedding fails."""
    if not text or not OPENAI_API_KEY:
        return None

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
        )
        return response.data[0].embedding
    except Exception as error:
        print(f"Embedding request failed: {error}")
        return None


def cosine_similarity(vector_a, vector_b):
    """Calculate cosine similarity while guarding against zero vectors."""
    vector_a = np.asarray(vector_a, dtype=float)
    vector_b = np.asarray(vector_b, dtype=float)
    denominator = np.linalg.norm(vector_a) * np.linalg.norm(vector_b)

    if denominator == 0:
        return 0.0

    return float(np.dot(vector_a, vector_b) / denominator)


def fetch_candidate_recipes(preferences, limit=20):
    """Fetch candidate recipes that satisfy the user's saved preferences."""
    params = {
        "apiKey": SPOONACULAR_API_KEY,
        "number": limit,
        "addRecipeInformation": "true",
    }

    diets = preferences.get("diets", [])
    intolerances = preferences.get("intolerances", [])
    cuisines = preferences.get("cuisines", [])

    if diets:
        params["diet"] = ",".join(diets)
    if intolerances:
        params["intolerances"] = ",".join(intolerances)
    if cuisines:
        params["cuisine"] = ",".join(cuisines)

    try:
        response = requests.get(
            "https://api.spoonacular.com/recipes/complexSearch",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.RequestException as error:
        print(f"Spoonacular request failed: {error}")
        return []


def generate_smart_recommendations(current_user):
    """Rank preference-filtered recipes against the user's saved recipes."""
    if not SPOONACULAR_API_KEY or not OPENAI_API_KEY:
        return jsonify({
            "error": "Recommendation service is not configured."
        }), 503

    email = current_user["email"]
    preferences = user_preferences_collection.find_one({"email": email}) or {}
    saved_recipes = list(saved_recipes_collection.find({"user_email": email}))

    saved_embeddings = []
    for recipe in saved_recipes:
        embedding = get_embedding(recipe.get("title", ""))
        if embedding is not None:
            saved_embeddings.append(embedding)

    user_profile = (
        np.mean(saved_embeddings, axis=0)
        if saved_embeddings
        else None
    )

    candidates = fetch_candidate_recipes(preferences)
    ranked_recipes = []

    for recipe in candidates:
        title = recipe.get("title", "")
        candidate_embedding = get_embedding(title)
        score = (
            cosine_similarity(user_profile, candidate_embedding)
            if user_profile is not None and candidate_embedding is not None
            else 0.0
        )

        ranked_recipes.append({
            "id": recipe.get("id"),
            "title": title,
            "image": recipe.get("image"),
            "readyInMinutes": recipe.get("readyInMinutes"),
            "servings": recipe.get("servings"),
            "score": score,
        })

    ranked_recipes.sort(key=lambda recipe: recipe["score"], reverse=True)

    return jsonify({
        "results": ranked_recipes[:5],
        "meta": {
            "candidate_count": len(candidates),
            "saved_recipe_count": len(saved_recipes),
            "personalized": user_profile is not None,
            "embedding_model": EMBEDDING_MODEL,
        },
    }), 200
