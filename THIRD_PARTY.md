# References and attribution

RepoTrail contains an original implementation. The following public work informed its design.

- OpenMinis informed provider configuration, masked credential inputs, and model discovery. Reference snapshot 1a71df0f381cefde80bf912b723de6f9831bc177. [Provider configuration](https://github.com/OpenMinis/OpenMinis/blob/1a71df0f381cefde80bf912b723de6f9831bc177/src/ios/Views/Providers/AddProviderView.swift) · [Model discovery](https://github.com/OpenMinis/OpenMinis/blob/1a71df0f381cefde80bf912b723de6f9831bc177/src/android/app/src/main/java/com/openminis/app/provider/openai/OpenAIModelsApi.kt)

- Microsoft CodeTour informed the tour export schema. MIT. [Repository](https://github.com/microsoft/codetour)
- PocketFlow Codebase Knowledge informed staged repository analysis. MIT. [Repository](https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge)
- Aider's repository-map documentation informed bounded context selection. Apache-2.0 for its code. [Repository](https://github.com/Aider-AI/aider)
- Repomix informed file filtering and repository preprocessing decisions. MIT. [Repository](https://github.com/yamadashy/repomix)
- GitHub Spec Kit informed the requirements-to-verification workflow. MIT. [Repository](https://github.com/github/spec-kit)
- matklad's ARCHITECTURE.md article informed module maps and architecture boundaries. [Article](https://matklad.github.io/2021/02/06/ARCHITECTURE.md.html)
- Frontend design guidance follows **unslop by Damir Sokolovsky (@heydamir)**, licensed **CC BY 4.0**. The guidance was adapted to a source-reading workspace with a grayscale interface, semantic accent, source panel and linked reading steps. [Author](https://x.com/heydamir) · [Skill](https://heydamir.design/unslop) · [License](https://creativecommons.org/licenses/by/4.0/)

The benchmark checkouts are downloaded separately and ignored by Git. Their original licenses apply. Source excerpts in local benchmark outputs retain their original copyright and license terms.

Team retrieval uses FastEmbed and the multilingual MiniLM model through its supported ONNX distribution. FastEmbed is Apache-2.0, pgvector is PostgreSQL-licensed, and the upstream multilingual MiniLM model is Apache-2.0. Model artifacts are downloaded to the local runtime cache. [FastEmbed](https://github.com/qdrant/fastembed) · [pgvector](https://github.com/pgvector/pgvector) · [Model](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)

