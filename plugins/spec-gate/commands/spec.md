---
description: Cria ou refina o SPEC.md de uma feature, o contrato do qual os testes black-box serão derivados
---

Crie ou atualize o SPEC.md da feature descrita pelo usuário: $ARGUMENTS

Siga estas regras ao escrever a spec:

1. Se `.specgate.json` não existir na raiz do projeto, crie-o primeiro perguntando ao usuário o comando de teste do projeto e os diretórios de código-fonte. Formato:

```json
{
  "test_command": "pytest -q",
  "source_paths": ["src"],
  "max_fix_attempts": 5
}
```

2. A spec descreve APENAS comportamento observável: entradas, saídas, efeitos visíveis, casos de borda e casos de erro, com valores concretos sempre que possível. Nada de detalhes de implementação, nomes de classes internas ou estrutura de módulos. O teste de qualidade da spec: um testador que nunca viu o código consegue escrever asserções com valores esperados concretos só de ler este documento?

3. Estrutura sugerida do SPEC.md:
   - **Objetivo**: uma frase
   - **Comportamentos**: lista numerada, cada item = dado X, quando Y, então Z (com valores)
   - **Casos de erro**: o que acontece com entrada inválida, estado inválido, falha de dependência
   - **Fora de escopo**: o que esta feature explicitamente NÃO faz
   - **Interfaces públicas**: assinaturas de CLI, endpoints ou API pública que a spec fixa (só a superfície pública, nunca o interior)

4. Enquanto escrever, se encontrar qualquer ponto que dependa de uma decisão que o usuário não tomou, PARE e pergunte antes de fixar na spec. Pergunta de uma linha, opções concretas. Nunca fixe um comportamento em cima de suposição silenciosa.

5. Ao terminar, apresente a spec ao usuário para aprovação explícita. Só depois da aprovação sugira rodar `/spec-gate:pipeline` para seguir com testes e implementação.
