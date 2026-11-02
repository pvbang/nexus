# Hướng dẫn sử dụng Free API trong Nexus

Nexus hỗ trợ nhiều LLM provider miễn phí, **không cần API key**, hoạt động ngay khi cài đặt (zero-config).

---

## Tổng quan Free Providers

| Provider | Base URL | Auth | Rate Limit | Models |
|----------|----------|------|-----------|--------|
| **Kilo Gateway** | `https://api.kilo.ai/api/gateway/v1` | Không (IP-based) | 200 req/hr/IP | kilo-auto/free, nemotron-120b, poolside-laguna |
| **Pollinations AI** | `https://text.pollinations.ai/openai` | Không | Unlimited* | openai, mistral, llama, qwen |
| **GPT4Free (g4f)** | Local library | Không | Tuỳ provider | gpt-4o, gpt-4, claude, llama-3 |

> *Pollinations không công bố rate limit cụ thể nhưng có throttling khi traffic cao.

---

## 1. Kilo Gateway (Recommended)

Provider mặc định, tự động route tới model free tốt nhất.

### Cấu hình `.env`

```env
CORE_LLM_PROVIDER=kilo
CORE_LLM_MODEL=kilo-auto/free
KILO_GATEWAY_ENABLED=true
KILO_GATEWAY_BASE_URL=https://api.kilo.ai/api/gateway/v1
```

### Gọi trực tiếp bằng curl

```bash
curl -X POST https://api.kilo.ai/api/gateway/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer anonymous" \
  -d '{
    "model": "kilo-auto/free",
    "messages": [{"role": "user", "content": "Xin chào!"}],
    "temperature": 0.7,
    "max_tokens": 1024
  }'
```

### Gọi bằng Python (OpenAI SDK)

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key="anonymous",
    base_url="https://api.kilo.ai/api/gateway/v1",
)

response = await client.chat.completions.create(
    model="kilo-auto/free",
    messages=[{"role": "user", "content": "Xin chào!"}],
    temperature=0.7,
    max_tokens=1024,
)
print(response.choices[0].message.content)
```

### Models miễn phí trên Kilo

| Model ID | Mô tả |
|----------|--------|
| `kilo-auto/free` | Auto-route tới model free tốt nhất hiện tại |
| `nvidia/nemotron-3-super-120b-a12b:free` | Nemotron 3 Super 120B (reasoning mạnh) |
| `poolside/laguna-m.1:free` | Poolside Laguna (code + general) |
| `openrouter/free` | OpenRouter free tier models |

### Streaming

```python
stream = await client.chat.completions.create(
    model="kilo-auto/free",
    messages=[{"role": "user", "content": "Viết một bài thơ ngắn"}],
    stream=True,
)
async for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

---

## 2. Pollinations AI

Provider free, OpenAI-compatible, không cần đăng ký.

### Cấu hình `.env`

```env
CORE_LLM_PROVIDER=pollinations
CORE_LLM_MODEL=openai
POLLINATION_ENABLED=true
POLLINATION_BASE_URL=https://text.pollinations.ai/openai
```

### Gọi trực tiếp bằng curl

```bash
curl -X POST https://text.pollinations.ai/openai/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai",
    "messages": [{"role": "user", "content": "Hello!"}],
    "temperature": 0.7,
    "max_tokens": 1024
  }'
```

### Gọi bằng Python

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key="not-needed",
    base_url="https://text.pollinations.ai/openai",
)

response = await client.chat.completions.create(
    model="openai",  # hoặc: mistral, llama, qwen
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

### Models trên Pollinations

| Model ID | Backend |
|----------|---------|
| `openai` | GPT-4o mini |
| `mistral` | Mistral Large |
| `llama` | Llama 3.x |
| `qwen` | Qwen 2.5 |

---

## 3. GPT4Free (g4f)

Library Python aggregates nhiều provider miễn phí. Cần cài thêm package.

### Cài đặt

```bash
pip install g4f
```

### Cấu hình `.env`

```env
G4F_ENABLED=true
```

### Gọi bằng Python

```python
import g4f
import asyncio

# Sync
response = g4f.ChatCompletion.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response)

# Async streaming
async def stream_g4f():
    response = g4f.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Write a poem"}],
        stream=True,
    )
    for chunk in response:
        print(chunk, end="")

asyncio.run(stream_g4f())
```

### Lưu ý g4f

- Không ổn định bằng Kilo/Pollinations (phụ thuộc third-party providers)
- Không hỗ trợ function calling / tool use
- Nên dùng làm fallback cuối cùng

---

## 4. Gọi qua Nexus API (OpenAI-Compatible)

Nexus expose endpoint `/v1/chat/completions` — hoạt động như OpenAI proxy, tự động fallback qua các free providers.

### Cấu hình client bên ngoài (Cursor, Continue.dev, LangChain, v.v.)

```env
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=nexus
```

### Gọi qua curl

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer nexus" \
  -d '{
    "model": "nexus",
    "messages": [{"role": "user", "content": "Giải thích quantum computing"}],
    "stream": true
  }'
```

### Gọi qua Python

```python
from openai import OpenAI

client = OpenAI(
    api_key="nexus",
    base_url="http://localhost:8000/v1",
)

response = client.chat.completions.create(
    model="nexus",
    messages=[{"role": "user", "content": "Xin chào!"}],
    stream=True,
)
for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

---

## 5. Fallback Chain (Tự động)

Hệ thống tự động failover qua các provider khi gặp lỗi:

```
Core Provider → Agent Provider → Kilo free models → Pollinations → g4f
```

Thứ tự chi tiết:

1. **Core LLM** (provider chính được cấu hình)
2. **Agent LLM** (nếu khác core)
3. **Kilo Gateway**: `kilo-auto/free` → `nemotron-120b:free` → `poolside-laguna:free`
4. **Pollinations**: `openai` → `mistral` → `llama` → `qwen`
5. **g4f**: `gpt-4o` → `gpt-4` → `claude` → `llama-3`

Mỗi provider có:
- **Circuit Breaker**: Tự động disable provider khi lỗi liên tục
- **Cooldown**: Tự phục hồi sau khoảng thời gian nghỉ
- **Retry + Exponential Backoff**: 3 lần retry với jitter

### Cấu hình fallback thủ công

```env
# Format: provider:model:api_key (api_key optional)
LLM_FALLBACK_PROVIDERS=openrouter:meta-llama/llama-3-8b-instruct:free,kilo:kilo-auto/free
```

---

## 6. Embedding miễn phí

### Option A: HuggingFace (offline, không cần API)

```env
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

Model tự download lần đầu, sau đó cache local. Hỗ trợ tiếng Việt.

### Option B: Local Ollama

```env
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://localhost:11434/v1
```

### Fallback tự động

Dù chọn provider nào, hệ thống luôn pre-load `Qwen3-Embedding-0.6B` làm fallback. Nếu primary fail → tự động dùng local model.

---

## 7. Search API miễn phí

| Service | Free Tier | Config |
|---------|-----------|--------|
| **DuckDuckGo** | Unlimited, không cần key | Built-in, luôn available |
| **Brave Search** | 2000 queries/tháng | `BRAVE_SEARCH_API_KEY=xxx` |
| **Serper.dev** | 2500 queries free | `SERPER_API_KEY=xxx` |
| **Tavily** | 1000 queries/tháng | `TAVILY_API_KEY=xxx` |
| **SearXNG** | Self-hosted, unlimited | `SEARXNG_BASE_URL=http://...` |

---

## 8. Quick Start (Zero Config)

Chạy ngay không cần API key nào:

```bash
# 1. Copy env
cp .env.example .env

# 2. Start services
docker compose up -d

# 3. Test API
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer nexus" \
  -d '{"model":"nexus","messages":[{"role":"user","content":"Hello!"}]}'
```

Mặc định đã cấu hình Kilo Gateway free — chạy được ngay.

---

## Tips & Best Practices

1. **Luôn bật ít nhất 2 free providers** để đảm bảo uptime:
   ```env
   KILO_GATEWAY_ENABLED=true
   POLLINATION_ENABLED=true
   G4F_ENABLED=true
   ```

2. **Kilo Gateway là lựa chọn tốt nhất** cho free tier: ổn định, hỗ trợ streaming, function calling.

3. **Rate limit 200 req/hr/IP** trên Kilo — đủ cho dev/testing. Production nên dùng paid provider.

4. **Pollinations** tốt cho tasks đơn giản, không cần function calling.

5. **g4f** không ổn định — chỉ dùng làm last resort.

6. **Local LLM (Ollama)** nếu có GPU — không giới hạn, privacy tốt:
   ```env
   LOCAL_LLM_ENABLED=true
   LOCAL_LLM_BASE_URL=http://localhost:11434/v1
   LOCAL_LLM_MODEL=qwen2.5:32b
   ```
