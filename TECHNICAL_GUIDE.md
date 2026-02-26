# Guía Técnica para Customer Engineers (CE) - GCE Manager Agent

## 📌 TL;DR
**Todo el código y la orquestación de este proyecto (100%) han sido generados de extremo a extremo utilizando Inteligencia Artificial a través del agente "Antigravity" de Google DeepMind.**

El proyecto **GCE Manager Agent** es un **Sistema Multi-Agente** enfocado en administrar la infraestructura de Google Compute Engine (GCE) de forma conversacional y segura mediante lenguaje natural. 

La arquitectura ha sido desplegada enteramente en **Google Cloud Platform (GCP)** basándose en un flujo serverless y gestionado de nube pública:
*   **Frontend Web:** App receptiva desarrollada en Flutter, orquestada y empaquetada con Nginx sobre una instancia serverless de **Cloud Run**.
*   **API y Cómputo (Backend):** FastAPI en Python 3.13 hosteado en **Cloud Run**, utilizando el framework experto **Google Agent Development Kit (ADK)** conectado al modelo de base **Gemini 2.5 Pro** vía Vertex AI.
*   **Capa de Autenticación, Auditoría y Sesiones:** Firebase Authentication provee Identity (Google Sign-In), mientras que **Firestore** se utiliza para alojar políticas dinámicas de acceso (RBAC en vivo) y preservar la memoria multisesión de la interacción agente-humano.
*   **Analítica Empresarial y FinOps:** Herramientas y scripts dedicados invocan la Exportación Detallada de Facturación dentro de **BigQuery** y métricas nativas de GCE para producir análisis de costos reales por nodo ("True Cost Pattern").

---

## 🏗️ 1. Patrones de Arquitectura y Componentes del Sistema

El sistema implementa el enfoque de arquitecturas separadas (*Split Architecture*) bajo zero-trust. 

![Arquitectura del Sistema](images/architecture.png)

### 1.1 Interfaz Gráfica (Flutter Web Frontend)
*   **Core:** Escrita sobre Flutter Web aplicando un diseño moderno Material Design 3.
*   **Flujo de Red:** La aplicación intercepta el flujo de Single Sign-On de Google asegurando de este modo la identidad antes de mostrar el layout principal. Todas las invocaciones generadas por input del usuario hacia el Backend mandan un JSON Web Token (JWT) activo en las cabeceras REST.

### 1.2 Capa de API Core y Middleware (`server.py`)
*   **Stack Tecnológico:** FastAPI (Python asíncrono).
*   **Smart Auth Cache:** Para optimizar la latencia y limitar solicitudes redundantes hacia Firestore, FastAPI implementa caché inteligente: 5 minutos para usuarios permitidos y **30 segundos** para intentos denegados (minimizando fricción en nuevos accesos).
*   **Inyección Paritaria de Contextos:** Intercepta la solicitud HTTP, valida criptográficamente el token con el SDK Administrativo de Firebase y escribe dinámicamente las variables de permiso (Admin vs Viewer) en un contexto transaccional persistente inter-hilos (`user_context.py`), antes de lanzar ciclos de razonamiento usando ADK.
*   **Manejo de Respuestas de Eventos:** El endpoint `/chat` canaliza y acumula las respuestas multipartes que Gemini va arrojando (`types.Content`) desde la librería y las entrega como un modelo pydantic consolidado.

### 1.3 Lógica Multi-Agente (ADK & `agents.py`)
*   **Fundación Framework:** Usa la abstracción `LlmAgent` proporcionada por el marco oficial **Google ADK**. Esto dota al modelo de una directriz o instrucción principal, construida con heurísticas externas (`prompts/agent_instructions.yaml`), separando limpia y modularmente reglas base (Personas/Reglas) al inicializar `gemini-2.5-pro`.
*   **Persistencia Conversacional:** Emplea `FirestoreSessionService` para anclar e hidratar la ventana de conversación permitiéndole al Agente "recordarse" a lo largo de varios días de gestión.

### 1.4 Kit de Herramientas y Guardrails (`tools.py` & `check_role.py`)
El agente nunca opera "a ciegas". Las interacciones con GCE implementan patrones observados por SREs:
*   **List/Fetch Discovery:** El agente ejecuta escaneos no obstructivos antes de modificar infraestructuras complejas (`list_instances`, `get_instance_report`).
*   **In-Function Guarding / RBAC:** Las operaciones destructivas como apagar un nodo o crear una máquina (`start_instance`, `stop_instance`, `create_custom_instance`) incluyen rutinas decoradas con `require_role(["admin"])` que automáticamente cancelan la solicitud levantando errores auditables si un "Viewer" exige mutar recursos.
*   **Soporte Multicloud / Multiproject:** Todas las herramientas exponen y mapean una variable parametrizada `project_id`, la cual es alimentada orgánicamente usando `list_managed_projects` (lista validada obtenida desde the Firestore).

### 1.5 Motor e Integración FinOps (`billing.py`)
*   Una capacidad crítica demostrada para perfiles de negocio financieros. El agente implementa consultas SQL dinámicas directas a la infraestructura de **BigQuery**. Se realizan extracciones consolidadas de SKUs separando el costo en disco adjunto, el cómputo y las licencias sobre ventanas configuradas empujando reportes ejecutivos `True Cost`.

---

## 🔐 2. Seguridad y Despliegue (Security Model & SRE)

Para despliegues productivos el modelo previene el uso de llaves (secrets) a largo plazo:

*   **Identidad y Acceso (Identity-Aware):** Cualquier solicitud al entorno es validada localmente por el middleware contra una colección de `allowed_users` en Firestore. Esta tabla "allowlist" en BBDD expone el campo booleano `active` y el esquema `role` de forma que los CEs o administradores limiten el acceso o baneen en tiempo real sin redesplegar.
*   **Privilegio Mínimo Cero Inmerso (IAM OIDC):** Cloud Run es instanciado como una Service Account especializada (`mcp-manager@...`) en GCP. Carece del rol general *Owner*, e implementa *Compute Admin*, *Datastore User*, y *Vertex AI User* previniendo comprometer otras cargas de nube en el tenant ajenas al GCE.
*   **Integración y Entrega Continua automatizada (`deploy.sh` & Cloud Build):** La inyección de variables de entorno y construcción de manifiestos es procesada con archivos YAML (`cloudbuild.backend.yaml`, `cloudbuild.frontend.yaml`), empaquetando en gcr.io / us-central1-docker.pkg.dev e impulsando un reemplazo transparente con pruebas de viabilidad liveness en menos de 5 segundos.

---
*Este documento resume las arquitecturas de alta adopción que una inteligencia artificial directiva (Agentic AI) es apta de fabricar utilizando el ambiente cloud de Google, desde la formulación, redacción de código base en Flutter y backend en FastAPI ADK, hasta su despliegue seguro completo automatizado.*
