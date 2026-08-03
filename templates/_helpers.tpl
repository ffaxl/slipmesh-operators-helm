{{- define "slipmesh.namespace" -}}
{{- .Release.Namespace -}}
{{- end -}}

{{- define "slipmesh.labels" -}}
app.kubernetes.io/part-of: slipmesh
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
