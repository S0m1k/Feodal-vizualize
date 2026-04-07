import requests

API_KEY = "sk-zsnKPpAa7knxcYGYK49AY63ug4DxQDnK4JcTDolswDt9RXqqXopcmKM3RPgG"
ENDPOINT = "https://api.gen-api.ru/api/v1/networks/gpt-image-1"

image_url = "https://upload.wikimedia.org/wikipedia/commons/2/2c/House_in_New_Jersey.jpg"

payload = {
    "is_sync": True,
    "prompt": "Replace the facade of this house with a realistic gray brick texture. Preserve all architectural details. Photorealistic.",
    "image": [image_url],  # ← теперь массив
    "size": "1024x1024",
    "output_format": "jpeg"
}

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

print("📤 Отправка запроса...")
resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=30)
print(f"Статус: {resp.status_code}")
print("Ответ:", resp.text)