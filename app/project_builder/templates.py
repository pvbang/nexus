"""
Nexus - Project Builder Templates.

Registry of project templates cho các tech stack phổ biến.
Cung cấp scaffold files, dependencies, và dev/build commands
để khởi tạo project nhanh chóng.
"""

from __future__ import annotations

import structlog

from app.project_builder.models import DependencySpec, FileSpec, TechStack

logger = structlog.get_logger(__name__)


class ProjectTemplateRegistry:
    """Registry of project templates for common tech stacks.

    Mỗi template chứa:
    - name, description, tech_stack
    - scaffold_files: list[dict] với path + content (dùng {project_name} placeholder)
    - dependencies: DependencySpec
    - dev_command, build_command, default_port
    """

    def __init__(self) -> None:
        self._templates: dict[str, dict] = {}
        self._register_defaults()

    def get_template(self, tech: str) -> dict | None:
        """Lấy template theo tên tech stack."""
        return self._templates.get(tech)

    def list_templates(self) -> list[str]:
        """Liệt kê tất cả template names có sẵn."""
        return list(self._templates.keys())

    def get_scaffold_files(
        self, tech: str, project_name: str
    ) -> list[FileSpec]:
        """Trả về danh sách FileSpec đã interpolate {project_name}.

        Returns empty list nếu template không tồn tại.
        """
        template = self._templates.get(tech)
        if not template:
            logger.warning("template_not_found", tech=tech)
            return []

        files: list[FileSpec] = []
        for f in template.get("scaffold_files", []):
            content = f.get("content", "").replace(
                "{project_name}", project_name
            )
            path = f.get("path", "").replace("{project_name}", project_name)
            files.append(
                FileSpec(
                    path=path,
                    content=content,
                    description=f.get("description", ""),
                    language=f.get("language", ""),
                )
            )
        return files

    # ------------------------------------------------------------------
    # Default templates registration
    # ------------------------------------------------------------------

    def _register_defaults(self) -> None:
        """Register all built-in templates."""
        self._register_react_vite_tailwind()
        self._register_next_app()
        self._register_fastapi()
        self._register_fullstack_react_fastapi()

    # ------------------------------------------------------------------
    # 1. React + Vite + Tailwind
    # ------------------------------------------------------------------

    def _register_react_vite_tailwind(self) -> None:
        self._templates["react-vite-tailwind"] = {
            "name": "React + Vite + Tailwind CSS",
            "description": "Modern React SPA with Vite bundler and Tailwind CSS styling.",
            "tech_stack": TechStack(
                frontend="react",
                backend="",
                database="",
                css_framework="tailwindcss",
                ai_integration="",
                package_manager="npm",
            ),
            "dependencies": DependencySpec(
                npm=["react", "react-dom", "react-router-dom"],
                npm_dev=[
                    "@types/react",
                    "@types/react-dom",
                    "@vitejs/plugin-react",
                    "autoprefixer",
                    "postcss",
                    "tailwindcss",
                    "typescript",
                    "vite",
                ],
                pip=[],
            ),
            "dev_command": "npm run dev -- --port {port}",
            "build_command": "npm run build",
            "default_port": 5173,
            "scaffold_files": [
                {
                    "path": "package.json",
                    "language": "json",
                    "description": "Node.js package manifest",
                    "content": _REACT_VITE_PACKAGE_JSON,
                },
                {
                    "path": "vite.config.ts",
                    "language": "typescript",
                    "description": "Vite configuration",
                    "content": _VITE_CONFIG,
                },
                {
                    "path": "tsconfig.json",
                    "language": "json",
                    "description": "TypeScript configuration",
                    "content": _TSCONFIG_JSON,
                },
                {
                    "path": "tailwind.config.js",
                    "language": "javascript",
                    "description": "Tailwind CSS configuration",
                    "content": _TAILWIND_CONFIG,
                },
                {
                    "path": "postcss.config.js",
                    "language": "javascript",
                    "description": "PostCSS configuration",
                    "content": _POSTCSS_CONFIG,
                },
                {
                    "path": "index.html",
                    "language": "html",
                    "description": "HTML entry point",
                    "content": _INDEX_HTML,
                },
                {
                    "path": "src/main.tsx",
                    "language": "tsx",
                    "description": "React entry point",
                    "content": _REACT_MAIN_TSX,
                },
                {
                    "path": "src/App.tsx",
                    "language": "tsx",
                    "description": "Root App component",
                    "content": _REACT_APP_TSX,
                },
                {
                    "path": "src/index.css",
                    "language": "css",
                    "description": "Global styles with Tailwind directives",
                    "content": _TAILWIND_INDEX_CSS,
                },
            ],
        }

    # ------------------------------------------------------------------
    # 2. Next.js App Router
    # ------------------------------------------------------------------

    def _register_next_app(self) -> None:
        self._templates["next-app"] = {
            "name": "Next.js App Router + Tailwind CSS",
            "description": "Full-stack Next.js with App Router, TypeScript, and Tailwind CSS.",
            "tech_stack": TechStack(
                frontend="next",
                backend="next",
                database="",
                css_framework="tailwindcss",
                ai_integration="",
                package_manager="npm",
            ),
            "dependencies": DependencySpec(
                npm=["next", "react", "react-dom"],
                npm_dev=[
                    "@types/node",
                    "@types/react",
                    "@types/react-dom",
                    "autoprefixer",
                    "postcss",
                    "tailwindcss",
                    "typescript",
                ],
                pip=[],
            ),
            "dev_command": "npm run dev -- --port {port}",
            "build_command": "npm run build",
            "default_port": 3000,
            "scaffold_files": [
                {
                    "path": "package.json",
                    "language": "json",
                    "description": "Node.js package manifest",
                    "content": _NEXT_PACKAGE_JSON,
                },
                {
                    "path": "next.config.js",
                    "language": "javascript",
                    "description": "Next.js configuration",
                    "content": _NEXT_CONFIG,
                },
                {
                    "path": "tsconfig.json",
                    "language": "json",
                    "description": "TypeScript configuration",
                    "content": _NEXT_TSCONFIG,
                },
                {
                    "path": "tailwind.config.js",
                    "language": "javascript",
                    "description": "Tailwind CSS configuration",
                    "content": _NEXT_TAILWIND_CONFIG,
                },
                {
                    "path": "postcss.config.js",
                    "language": "javascript",
                    "description": "PostCSS configuration",
                    "content": _POSTCSS_CONFIG,
                },
                {
                    "path": "app/layout.tsx",
                    "language": "tsx",
                    "description": "Root layout component",
                    "content": _NEXT_LAYOUT,
                },
                {
                    "path": "app/page.tsx",
                    "language": "tsx",
                    "description": "Home page",
                    "content": _NEXT_PAGE,
                },
                {
                    "path": "app/globals.css",
                    "language": "css",
                    "description": "Global styles with Tailwind directives",
                    "content": _TAILWIND_INDEX_CSS,
                },
            ],
        }

    # ------------------------------------------------------------------
    # 3. FastAPI
    # ------------------------------------------------------------------

    def _register_fastapi(self) -> None:
        self._templates["fastapi"] = {
            "name": "FastAPI Backend",
            "description": "Python FastAPI backend with SQLAlchemy and Pydantic.",
            "tech_stack": TechStack(
                frontend="",
                backend="fastapi",
                database="sqlite",
                css_framework="",
                ai_integration="",
                package_manager="pip",
            ),
            "dependencies": DependencySpec(
                npm=[],
                npm_dev=[],
                pip=[
                    "fastapi>=0.115.0",
                    "uvicorn[standard]>=0.32.0",
                    "sqlalchemy>=2.0.0",
                    "pydantic>=2.0.0",
                    "pydantic-settings>=2.0.0",
                    "python-dotenv>=1.0.0",
                ],
            ),
            "dev_command": "uvicorn app.main:app --reload --port {port}",
            "build_command": "",
            "default_port": 8000,
            "scaffold_files": [
                {
                    "path": "requirements.txt",
                    "language": "text",
                    "description": "Python dependencies",
                    "content": _FASTAPI_REQUIREMENTS,
                },
                {
                    "path": "main.py",
                    "language": "python",
                    "description": "Application entry point",
                    "content": _FASTAPI_MAIN_ENTRY,
                },
                {
                    "path": "config.py",
                    "language": "python",
                    "description": "Application configuration",
                    "content": _FASTAPI_CONFIG,
                },
                {
                    "path": "app/__init__.py",
                    "language": "python",
                    "description": "App package init",
                    "content": '"""App package for {project_name}."""\n',
                },
                {
                    "path": "app/main.py",
                    "language": "python",
                    "description": "FastAPI application factory",
                    "content": _FASTAPI_APP_MAIN,
                },
                {
                    "path": "app/models.py",
                    "language": "python",
                    "description": "SQLAlchemy models",
                    "content": _FASTAPI_MODELS,
                },
                {
                    "path": "app/api/__init__.py",
                    "language": "python",
                    "description": "API package init",
                    "content": '"""API routes for {project_name}."""\n',
                },
                {
                    "path": "app/api/routes.py",
                    "language": "python",
                    "description": "API route definitions",
                    "content": _FASTAPI_ROUTES,
                },
            ],
        }

    # ------------------------------------------------------------------
    # 4. Fullstack React + FastAPI
    # ------------------------------------------------------------------

    def _register_fullstack_react_fastapi(self) -> None:
        self._templates["fullstack-react-fastapi"] = {
            "name": "Fullstack React + FastAPI",
            "description": "Full-stack with React/Vite/Tailwind frontend and FastAPI backend, orchestrated with Docker Compose.",
            "tech_stack": TechStack(
                frontend="react",
                backend="fastapi",
                database="sqlite",
                css_framework="tailwindcss",
                ai_integration="",
                package_manager="npm",
            ),
            "dependencies": DependencySpec(
                npm=["react", "react-dom", "react-router-dom"],
                npm_dev=[
                    "@types/react",
                    "@types/react-dom",
                    "@vitejs/plugin-react",
                    "autoprefixer",
                    "postcss",
                    "tailwindcss",
                    "typescript",
                    "vite",
                ],
                pip=[
                    "fastapi>=0.115.0",
                    "uvicorn[standard]>=0.32.0",
                    "sqlalchemy>=2.0.0",
                    "pydantic>=2.0.0",
                    "pydantic-settings>=2.0.0",
                    "python-dotenv>=1.0.0",
                ],
            ),
            "dev_command": "docker compose up",
            "build_command": "docker compose build",
            "default_port": 5173,
            "scaffold_files": [
                # -- Docker orchestration --
                {
                    "path": "docker-compose.yml",
                    "language": "yaml",
                    "description": "Docker Compose for frontend + backend",
                    "content": _FULLSTACK_DOCKER_COMPOSE,
                },
                # -- Frontend files (in frontend/) --
                {
                    "path": "frontend/package.json",
                    "language": "json",
                    "description": "Frontend package manifest",
                    "content": _FULLSTACK_FE_PACKAGE_JSON,
                },
                {
                    "path": "frontend/vite.config.ts",
                    "language": "typescript",
                    "description": "Vite config with API proxy",
                    "content": _FULLSTACK_FE_VITE_CONFIG,
                },
                {
                    "path": "frontend/tsconfig.json",
                    "language": "json",
                    "description": "TypeScript configuration",
                    "content": _TSCONFIG_JSON,
                },
                {
                    "path": "frontend/tailwind.config.js",
                    "language": "javascript",
                    "description": "Tailwind CSS configuration",
                    "content": _TAILWIND_CONFIG,
                },
                {
                    "path": "frontend/postcss.config.js",
                    "language": "javascript",
                    "description": "PostCSS configuration",
                    "content": _POSTCSS_CONFIG,
                },
                {
                    "path": "frontend/index.html",
                    "language": "html",
                    "description": "HTML entry point",
                    "content": _INDEX_HTML,
                },
                {
                    "path": "frontend/src/main.tsx",
                    "language": "tsx",
                    "description": "React entry point",
                    "content": _REACT_MAIN_TSX,
                },
                {
                    "path": "frontend/src/App.tsx",
                    "language": "tsx",
                    "description": "Root App component",
                    "content": _REACT_APP_TSX,
                },
                {
                    "path": "frontend/src/index.css",
                    "language": "css",
                    "description": "Global styles",
                    "content": _TAILWIND_INDEX_CSS,
                },
                # -- Backend files (in backend/) --
                {
                    "path": "backend/requirements.txt",
                    "language": "text",
                    "description": "Python dependencies",
                    "content": _FASTAPI_REQUIREMENTS,
                },
                {
                    "path": "backend/main.py",
                    "language": "python",
                    "description": "Backend entry point",
                    "content": _FASTAPI_MAIN_ENTRY,
                },
                {
                    "path": "backend/config.py",
                    "language": "python",
                    "description": "Backend configuration",
                    "content": _FASTAPI_CONFIG,
                },
                {
                    "path": "backend/app/__init__.py",
                    "language": "python",
                    "description": "App package init",
                    "content": '"""App package for {project_name}."""\n',
                },
                {
                    "path": "backend/app/main.py",
                    "language": "python",
                    "description": "FastAPI application",
                    "content": _FASTAPI_APP_MAIN,
                },
                {
                    "path": "backend/app/models.py",
                    "language": "python",
                    "description": "SQLAlchemy models",
                    "content": _FASTAPI_MODELS,
                },
                {
                    "path": "backend/app/api/__init__.py",
                    "language": "python",
                    "description": "API package init",
                    "content": '"""API routes for {project_name}."""\n',
                },
                {
                    "path": "backend/app/api/routes.py",
                    "language": "python",
                    "description": "API routes",
                    "content": _FASTAPI_ROUTES,
                },
            ],
        }


# ===================================================================
# Shared template content constants
# ===================================================================

# -- React + Vite --

_REACT_VITE_PACKAGE_JSON = """{
  "name": "{project_name}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.7.0",
    "vite": "^6.0.0"
  }
}
"""

_VITE_CONFIG = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
});
"""

_TSCONFIG_JSON = """{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true
  },
  "include": ["src"]
}
"""

_TAILWIND_CONFIG = """/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
"""

_POSTCSS_CONFIG = """export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""

_INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{project_name}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

_REACT_MAIN_TSX = """import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
"""

_REACT_APP_TSX = """export default function App() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">
          {project_name}
        </h1>
        <p className="text-lg text-gray-600">
          Edit <code className="bg-gray-200 px-1 rounded">src/App.tsx</code> to get started.
        </p>
      </div>
    </div>
  );
}
"""

_TAILWIND_INDEX_CSS = """@tailwind base;
@tailwind components;
@tailwind utilities;
"""

# -- Next.js App Router --

_NEXT_PACKAGE_JSON = """{
  "name": "{project_name}",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start"
  },
  "dependencies": {
    "next": "^15.0.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.7.0"
  }
}
"""

_NEXT_CONFIG = """/** @type {import('next').NextConfig} */
const nextConfig = {};

module.exports = nextConfig;
"""

_NEXT_TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
"""

_NEXT_TAILWIND_CONFIG = """/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
"""

_NEXT_LAYOUT = """import type {{ Metadata }} from "next";
import "./globals.css";

export const metadata: Metadata = {{
  title: "{project_name}",
  description: "Generated by Nexus Project Builder",
}};

export default function RootLayout({{
  children,
}}: {{
  children: React.ReactNode;
}}) {{
  return (
    <html lang="en">
      <body>{{children}}</body>
    </html>
  );
}}
"""

_NEXT_PAGE = """export default function Home() {{
  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">
          {project_name}
        </h1>
        <p className="text-lg text-gray-600">
          Edit <code className="bg-gray-200 px-1 rounded">app/page.tsx</code> to get started.
        </p>
      </div>
    </main>
  );
}}
"""

# -- FastAPI --

_FASTAPI_REQUIREMENTS = """fastapi>=0.115.0
uvicorn[standard]>=0.32.0
sqlalchemy>=2.0.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
python-dotenv>=1.0.0
"""

_FASTAPI_MAIN_ENTRY = """\"\"\"Entry point — run with: uvicorn main:app --reload.\"\"\"

from app.main import app  # noqa: F401
"""

_FASTAPI_CONFIG = """\"\"\"Application configuration via environment variables.\"\"\"

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "{project_name}"
    debug: bool = True
    database_url: str = "sqlite:///./data.db"

    class Config:
        env_file = ".env"


settings = Settings()
"""

_FASTAPI_APP_MAIN = """\"\"\"FastAPI application factory.\"\"\"

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router


def create_app() -> FastAPI:
    application = FastAPI(
        title="{project_name}",
        version="0.1.0",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router, prefix="/api")
    return application


app = create_app()
"""

_FASTAPI_MODELS = """\"\"\"SQLAlchemy models.\"\"\"

from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, default="")


engine = create_engine(settings.database_url, echo=settings.debug)
SessionLocal = sessionmaker(bind=engine)

Base.metadata.create_all(bind=engine)
"""

_FASTAPI_ROUTES = """\"\"\"API route definitions.\"\"\"

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.get("/items")
async def list_items():
    return {"items": []}
"""

# -- Fullstack Docker Compose --

_FULLSTACK_DOCKER_COMPOSE = """version: "3.9"

services:
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "5173:5173"
    volumes:
      - ./frontend/src:/app/src
    depends_on:
      - backend
    command: npm run dev -- --host 0.0.0.0 --port 5173

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app
    command: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

_FULLSTACK_FE_PACKAGE_JSON = """{
  "name": "{project_name}-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.7.0",
    "vite": "^6.0.0"
  }
}
"""

_FULLSTACK_FE_VITE_CONFIG = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://backend:8000",
        changeOrigin: true,
      },
    },
  },
});
"""
