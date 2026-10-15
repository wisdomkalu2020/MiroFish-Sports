"""\n配置管理\n统一从项目根目录的 .env 文件加载配置\n"""

import os
from urllib.parse import urlsplit
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    # Per-run gateway settings injected by the parent process take precedence.
    load_dotenv(project_root_env, override=False)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=False)


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示
    JSON_AS_ASCII = False
    
    # Both local and remote runtimes use the OpenAI-compatible request format.
    # Remote API use is opt-in. The real OpenAI key is never a local SDK key.
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'local').strip().lower()
    LLM_API_KEY = (os.environ.get('LLM_API_KEY') if LLM_PROVIDER == 'openai'
                   else 'mirofish-local')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', '').strip()
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', '').strip()
    LLM_REPORT_MODEL_NAME = os.environ.get('LLM_REPORT_MODEL_NAME', '').strip() or LLM_MODEL_NAME
    LLM_MAX_OUTPUT_TOKENS = int(os.environ.get('LLM_MAX_OUTPUT_TOKENS', '4096'))
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def resolve_llm_settings(
        cls,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> tuple[str, str, str]:
        """Return safe connection settings for one LLM client.

        A local run can only use the budget gateway's token and endpoint. It
        cannot inherit an OpenAI Platform key or call the model server directly.
        """
        from .utils.local_llm_context import active_llm_settings

        active = active_llm_settings() or {}
        provider = cls.LLM_PROVIDER
        if provider not in {'local', 'openai'}:
            raise ValueError('LLM_PROVIDER must be local or openai')
        if provider == 'local':
            endpoint = (active.get('base_url') or cls.LLM_BASE_URL or '').strip()
            chosen_model = (active.get('model') or cls.LLM_MODEL_NAME or '').strip()
            token = active.get('api_key') or os.environ.get('MIROFISH_LOCAL_GATEWAY_TOKEN')
            if not endpoint:
                raise ValueError('LLM_BASE_URL is required')
            if not chosen_model:
                raise ValueError('LLM_MODEL_NAME is required')
            if not token:
                raise ValueError('Local LLM calls require an active run budget gateway')
            if base_url and base_url.rstrip('/') != endpoint.rstrip('/'):
                raise ValueError('Local LLM endpoint must match the run gateway')
            if model and model != chosen_model:
                raise ValueError('Local LLM model must match the run gateway')
            if api_key and api_key != token:
                raise ValueError('Local LLM API key must match the run gateway token')
            parsed = urlsplit(endpoint)
            if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                    parsed.path.rstrip('/') != '/v1' or parsed.port is None or
                    parsed.username is not None or parsed.password is not None or
                    parsed.query or parsed.fragment):
                raise ValueError('Local LLM_BASE_URL must use an HTTP loopback /v1 endpoint')
            return token, endpoint, chosen_model
        endpoint = (base_url or active.get('base_url') or cls.LLM_BASE_URL or '').strip()
        chosen_model = (model or active.get('model') or cls.LLM_MODEL_NAME or '').strip()
        if not endpoint:
            raise ValueError('LLM_BASE_URL is required')
        if not chosen_model:
            raise ValueError('LLM_MODEL_NAME is required')
        key = api_key or active.get('api_key') or cls.LLM_API_KEY
        if not key:
            raise ValueError('LLM_API_KEY is required for remote provider')
        return key, endpoint, chosen_model
    
    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置"""
        errors: list[str] = []
        if cls.LLM_PROVIDER not in {'local', 'openai'}:
            errors.append('LLM_PROVIDER must be local or openai')
        elif cls.LLM_PROVIDER == 'local':
            if not cls.LLM_MODEL_NAME:
                errors.append('LLM_MODEL_NAME is required')
        else:
            try:
                cls.resolve_llm_settings()
            except ValueError as error:
                errors.append(str(error))
        if cls.LLM_MAX_OUTPUT_TOKENS < 1:
            errors.append('LLM_MAX_OUTPUT_TOKENS must be positive')
        # Historical sports imports, statistical evaluation, and ontology
        # generation do not need Zep. Graph and simulation endpoints check
        # ZEP_API_KEY before invoking the existing Cloud memory pipeline.
        if os.environ.get("ZEP_API_URL"):
            errors.append("ZEP_API_URL 不受支持；MiroFish 仅连接 Zep Cloud")
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors
