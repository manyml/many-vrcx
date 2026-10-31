# Many VRCX

Programa para Windows que lista as festas do grupo Many no histórico do VRCX e gera um arquivo só com as festas escolhidas, para enviar na aba Métricas do Many Planner.

- O banco do VRCX é lido sem alterações.
- O arquivo só leva, de cada festa escolhida: mundo, horários, lotação e quem estava (nome, ID do VRChat e tempo).
- Ao abrir, o programa consulta a última versão neste repositório e oferece a atualização.

## Baixar

[ManyVRCX.exe](https://github.com/manyml/many-vrcx/releases/latest/download/ManyVRCX.exe). O Windows pode mostrar um aviso por ser um programa sem assinatura digital: clique em "Mais informações" e depois em "Executar assim mesmo".

## Publicar uma versão

1. Aumente `VERSAO` em `extrator.py`.
2. Rode `gerar-exe.cmd`.
3. Crie um Release com a tag `vX.Y.Z` e anexe o `ManyVRCX.exe`.
