"""Ollama address validation and read-only model discovery."""

import ipaddress
import json
import re
import urllib.error
import urllib.request

from django import forms


class OllamaAddressForm(forms.Form):
    ip = forms.CharField(max_length=255, error_messages={"required": "请填写 Ollama 主机地址。"})
    port = forms.IntegerField(
        min_value=1,
        max_value=65535,
        error_messages={
            "required": "请填写 Ollama 端口。",
            "invalid": "端口必须是整数。",
            "min_value": "端口必须介于 1 和 65535 之间。",
            "max_value": "端口必须介于 1 和 65535 之间。",
        },
    )

    def clean_ip(self):
        host = self.cleaned_data["ip"]
        bracketed = host.startswith("[") and host.endswith("]")
        if bracketed:
            host = host[1:-1]
        if "%" not in host:
            try:
                return str(ipaddress.ip_address(host))
            except ValueError:
                pass
        labels = host.rstrip(".").split(".")
        if (
            len(host) <= 253
            and not bracketed
            and not re.fullmatch(r"\d+(?:\.\d+){3}", host)
            and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels)
        ):
            return host.lower()
        raise forms.ValidationError("主机地址必须是 IP 或主机名，不能包含 URL、路径或登录信息。")

    @property
    def base_url(self):
        host = self.cleaned_data["ip"]
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.cleaned_data['port']}"


class OllamaModelListError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def fetch_ollama_models(base_url, timeout=5):
    """Read /api/tags and normalize the installed model names."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise OllamaModelListError("获取模型列表超时，请检查 Ollama 服务后重试。", 504) from exc
    except urllib.error.HTTPError as exc:
        raise OllamaModelListError(f"获取模型列表失败：Ollama 返回 HTTP {exc.code}。") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise OllamaModelListError("获取模型列表超时，请检查 Ollama 服务后重试。", 504) from exc
        raise OllamaModelListError("无法连接 Ollama 服务，请检查主机地址、端口和服务状态。") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise OllamaModelListError("Ollama 返回的模型列表格式无效。") from exc
    except OSError as exc:
        raise OllamaModelListError("读取 Ollama 模型列表失败，请检查服务状态。") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise OllamaModelListError("Ollama 返回的模型列表格式无效。")

    names = []
    seen = set()
    for entry in payload["models"]:
        if not isinstance(entry, dict):
            continue
        for field in ("name", "model"):
            name = entry.get(field)
            if isinstance(name, str):
                name = name.strip()
                if name and not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in name):
                    if name not in seen:
                        seen.add(name)
                        names.append(name)
                    break
    return names
