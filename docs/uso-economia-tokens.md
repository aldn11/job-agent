# Registro de uso da IA e economia de tokens

O pipeline grava uma linha em `ai_usage` para cada tentativa iniciada pelo
Hermes. O registro inclui o ID interno da vaga, data, rótulo do modelo, tamanho
do prompt e da resposta, estimativas de tokens, resultado e tipo de erro.
O texto da vaga e a resposta do modelo **não** são gravados nessa tabela.

## Limites e interpretação

- Vagas com URL HTTPS inválida (quando informada) não são enviadas ao Hermes e
  passam para `INPUT_REVIEW`. Consulte esses casos com:

  ```sql
  SELECT id, source, url, evaluation_last_error
  FROM jobs
  WHERE status = 'INPUT_REVIEW'
  ORDER BY first_seen DESC;
  ```

  URL vazia continua permitida para anúncios colados manualmente.
- Vagas sem `JOB TEXT`, com menos de 100 caracteres ou menos de 15 palavras não
  são enviadas ao Hermes. Links de anúncios do LinkedIn voltam para
  `SCRAPE_PENDING`; outras fontes vão para `SCRAPE_REVIEW`.
- Os coletores usam URL canônica para impedir reavaliação da mesma vaga. A
  entrada manual sem URL usa uma chave baseada em empresa, título e texto exato.
  Coincidências apenas de empresa/título continuam como possíveis duplicatas:
  não são descartadas automaticamente, pois podem ser anúncios distintos.
- `MAX_JOB_DESCRIPTION_CHARS` limita a descrição enviada ao modelo. O padrão é
  `12000` caracteres. Se o texto for maior, o final é removido e o prompt avisa
  Vargas que o conteúdo foi truncado.
- `AI_MODEL_NAME` é apenas um rótulo para os relatórios. Ele não altera o modelo
  configurado no Hermes. Preencha-o no `.env` para identificar o modelo usado.
- `input_tokens_estimate` e `output_tokens_estimate` são aproximações
  (`ceil(caracteres / 4)`). Não equivalem ao uso faturado: não incluem todo o
  contexto interno do Hermes, tokens de sistema, chamadas internas adicionais
  nem a tokenização real do provedor.
- Uma linha representa uma execução do processo Hermes, não necessariamente uma
  única chamada ao provedor.
- `AI_QUOTA_RETRY_SECONDS` controla o intervalo para nova tentativa após um
  erro reconhecido como cota/limite (HTTP 429, `RESOURCE_EXHAUSTED` ou rate
  limit). O padrão de 86400 segundos evita gastar chamadas repetidas durante a
  mesma janela diária. O pipeline interrompe novas avaliações nessa execução.

Para conferir as avaliações mais recentes:

```sql
SELECT recorded_at, job_id, model, prompt_chars, input_tokens_estimate,
       response_chars, output_tokens_estimate, outcome, error_type
FROM ai_usage
ORDER BY id DESC
LIMIT 50;
```

Para encontrar os maiores prompts:

```sql
SELECT job_id, model, prompt_chars, input_tokens_estimate,
       response_chars, output_tokens_estimate, outcome
FROM ai_usage
ORDER BY prompt_chars DESC
LIMIT 20;
```

Para obter um resumo por modelo e resultado:

```sql
SELECT model, outcome, COUNT(*) AS attempts,
       SUM(prompt_chars) AS prompt_chars,
       SUM(input_tokens_estimate) AS estimated_input_tokens,
       SUM(output_tokens_estimate) AS estimated_output_tokens
FROM ai_usage
GROUP BY model, outcome
ORDER BY model, outcome;
```

Esses dados servem para comparar tamanhos e falhas entre execuções. Para custo
real, consulte também o painel de uso do provedor: a estimativa local não
substitui os números de tokens informados por ele.

## Filtros determinísticos seguros

Use Python antes da IA para as condições objetivas implementadas: descrição
ausente, URL HTTPS inválida e duplicata exata. Não descarte por localização,
empresa/título parecidos ou termos de cargo; esses sinais podem ser ambíguos.
A adequação do perfil e requisitos escritos em linguagem natural continuam
sendo avaliados por Vargas.
