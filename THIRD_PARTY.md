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

## Public evaluation material

The public evaluation corpus references Requests (Apache-2.0), Starlette (BSD-3-Clause), and the FastAPI Full Stack Template (MIT). Checkouts remain separate downloads. README screenshots contain generated explanations and source excerpts from the pinned FastAPI template. [Corpus manifest](benchmarks/public-onboarding.json) · [Upstream template license](https://github.com/fastapi/full-stack-fastapi-template/blob/d40de23896d27d15c17a7bf9649123fd167a0aa8/LICENSE)

### FastAPI template copyright and permission notice

```text
MIT License

Copyright (c) 2019 Sebastián Ramírez

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
