#!/bin/bash
# Quick start script for Digital Twin data pipeline

echo "=========================================="
echo "Digital Twin - Quick Start"
echo "=========================================="
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

echo "✅ Docker is running"
echo ""

# Start services
echo "🚀 Starting Docker services..."
docker-compose up -d

echo ""
echo "⏳ Waiting for services to become healthy (60 seconds)..."
sleep 60

echo ""
echo "📊 Service Status:"
docker-compose ps

echo ""
echo "=========================================="
echo "✅ Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Run test publisher:"
echo "   python src/data/test_publisher.py --duration 30"
echo ""
echo "2. Access InfluxDB UI:"
echo "   http://localhost:8086"
echo "   Username: admin"
echo "   Password: adminpassword123"
echo ""
echo "3. View logs:"
echo "   docker-compose logs -f"
echo ""
echo "4. Stop services:"
echo "   docker-compose down"
echo ""
