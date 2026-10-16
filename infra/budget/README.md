# Alarma de presupuesto (AWS Budgets) — techo acordado de USD 20

El equipo del proyecto fijó un techo de costo **combinado** (AWS + ejecución
de modelos de IA) de **menos de USD 20** para esta PoC. Este directorio
automatiza la mitad de ese techo que sí es necesario vigilar activamente:
el gasto en AWS, que es **pospago** (se factura después de usarse, sin límite
automático salvo que tú lo configures). Ver el desglose completo en
`GUIA_DESPLIEGUE_Y_PRUEBAS.md`, sección 4.2.

No se incluyó como recurso dentro de `infra/template.yaml` porque
`AWS::Budgets::Budget` es un recurso "global" que AWS recomienda administrar
desde una pila en `us-east-1`, independiente de la región donde despliegues
la aplicación — mezclarlo en el mismo stack acoplaría innecesariamente el
despliegue de la app a esa región.

## Cómo crearla

1. Edita `notifications-with-subscribers.json` y reemplaza
   `TU-CORREO@ejemplo.com` por tu correo real (las 3 veces que aparece).
2. Opcional: edita `budget.json` si quieres un límite distinto a USD 15
   (dejando margen dentro del techo total de USD 20, que también cubre los
   créditos prepagados de OpenRouter).
3. Ejecuta (necesitas tu Account ID de AWS: `aws sts get-caller-identity --query Account --output text`):

   ```bash
   aws budgets create-budget \
     --account-id <TU_ACCOUNT_ID> \
     --budget file://budget.json \
     --notifications-with-subscribers file://notifications-with-subscribers.json \
     --region us-east-1
   ```

4. Confirma el correo de suscripción que AWS Budgets te enviará (SNS pide
   confirmación antes de empezar a notificar).

## Verificar o borrar

```bash
aws budgets describe-budgets --account-id <TU_ACCOUNT_ID> --region us-east-1
aws budgets delete-budget --account-id <TU_ACCOUNT_ID> \
  --budget-name javeriana-ia-poc-monthly-budget --region us-east-1
```

## Importante: esto alerta, no detiene nada automáticamente

AWS Budgets con `NotificationsWithSubscribers` solo **avisa por correo** al
cruzar los umbrales (50%, 80%, 100% pronosticado) — no apaga ni bloquea
recursos por sí solo. Si se quiere un freno automático (por ejemplo, aplicar
una política que impida crear más recursos al llegar al 100%), AWS Budgets
soporta "Budget Actions", pero no se incluyó aquí para no arriesgar que la
PoC quede inutilizable a mitad de una demo por una acción automática mal
calibrada. La mitigación más simple y segura sigue siendo correr
`sam delete` (sección 9 de la guía) en cuanto termines de probar — es lo que
más "quema" presupuesto (la instancia RDS corre 24/7 hasta que la borras).
