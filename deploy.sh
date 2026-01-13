#!/bin/bash
set -e

PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
BACKEND_SERVICE="gce-mcp-backend"
FRONTEND_SERVICE="gce-mcp-frontend"

echo "🚀 Deploying GCE Manager Agent to Cloud Run..."
echo "Project: $PROJECT_ID"
echo "Region: $REGION"

# 1. Deploy Backend
echo "--------------------------------------------------"
echo "📦 Building and Deploying Backend ($BACKEND_SERVICE)..."
gcloud builds submit --config cloudbuild.backend.yaml .

gcloud run deploy $BACKEND_SERVICE \
    --image us-central1-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/gce-mcp-backend \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,GOOGLE_GENAI_USE_VERTEXAI=true \
    --service-account=mcp-manager@$PROJECT_ID.iam.gserviceaccount.com

# Get Backend URL
BACKEND_URL=$(gcloud run services describe $BACKEND_SERVICE --region $REGION --format 'value(status.url)')
echo "✅ Backend deployed at: $BACKEND_URL"

# 2. Deploy Frontend
echo "--------------------------------------------------"
echo "🎨 Building and Deploying Frontend ($FRONTEND_SERVICE)..."
echo "⚠️  Updating Frontend to point to Backend URL: $BACKEND_URL"
# MacOS sed requires empty extension for -i
sed -i '' "s|http://localhost:8080/chat|$BACKEND_URL/chat|g" frontend/lib/chat_screen.dart

# Build container
gcloud builds submit --config cloudbuild.frontend.yaml .

# Deploy the container
gcloud run deploy $FRONTEND_SERVICE \
    --image us-central1-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/gce-mcp-frontend \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated

# Revert local change to localhost for dev
sed -i '' "s|$BACKEND_URL/chat|http://localhost:8080/chat|g" frontend/lib/chat_screen.dart

FRONTEND_URL=$(gcloud run services describe $FRONTEND_SERVICE --region $REGION --format 'value(status.url)')

echo "--------------------------------------------------"
echo "✨ Deployment Complete!"
echo "🌍 Frontend: $FRONTEND_URL"
echo "🔌 Backend:  $BACKEND_URL"
echo "--------------------------------------------------"
