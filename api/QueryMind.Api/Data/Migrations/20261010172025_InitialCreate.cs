using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace QueryMind.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class InitialCreate : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AlterDatabase()
                .Annotation("Npgsql:PostgresExtension:citext", ",,");

            migrationBuilder.CreateTable(
                name: "users",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    email = table.Column<string>(type: "citext", nullable: false),
                    password_hash = table.Column<string>(type: "text", nullable: false),
                    role = table.Column<string>(type: "text", nullable: false, defaultValue: "user"),
                    created_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false, defaultValueSql: "now()")
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_users", x => x.id);
                    table.CheckConstraint("ck_users_role", "role IN ('user', 'admin')");
                });

            migrationBuilder.CreateTable(
                name: "query_history",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<Guid>(type: "uuid", nullable: false),
                    question = table.Column<string>(type: "text", nullable: false),
                    status = table.Column<string>(type: "text", nullable: false),
                    final_sql = table.Column<string>(type: "text", nullable: true),
                    message = table.Column<string>(type: "text", nullable: true),
                    row_count = table.Column<int>(type: "integer", nullable: true),
                    truncated = table.Column<bool>(type: "boolean", nullable: true),
                    chart_type = table.Column<string>(type: "text", nullable: true),
                    linking_ms = table.Column<int>(type: "integer", nullable: true),
                    generation_ms = table.Column<int>(type: "integer", nullable: true),
                    validation_ms = table.Column<int>(type: "integer", nullable: true),
                    execution_ms = table.Column<int>(type: "integer", nullable: true),
                    summary_ms = table.Column<int>(type: "integer", nullable: true),
                    engine_total_ms = table.Column<int>(type: "integer", nullable: true),
                    total_ms = table.Column<int>(type: "integer", nullable: true),
                    prompt_tokens = table.Column<int>(type: "integer", nullable: true),
                    completion_tokens = table.Column<int>(type: "integer", nullable: true),
                    model = table.Column<string>(type: "text", nullable: true),
                    correlation_id = table.Column<string>(type: "text", nullable: false),
                    rerun_of_id = table.Column<Guid>(type: "uuid", nullable: true),
                    created_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false, defaultValueSql: "now()")
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_query_history", x => x.id);
                    table.CheckConstraint("ck_query_history_status", "status IN ('success', 'failed', 'blocked', 'cannot_answer', 'error')");
                    table.ForeignKey(
                        name: "fk_query_history_query_history_rerun_of_id",
                        column: x => x.rerun_of_id,
                        principalTable: "query_history",
                        principalColumn: "id",
                        onDelete: ReferentialAction.SetNull);
                    table.ForeignKey(
                        name: "fk_query_history_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "query_attempts",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    history_id = table.Column<Guid>(type: "uuid", nullable: false),
                    attempt_no = table.Column<short>(type: "smallint", nullable: false),
                    sql = table.Column<string>(type: "text", nullable: true),
                    stage = table.Column<string>(type: "text", nullable: false),
                    error_code = table.Column<string>(type: "text", nullable: true),
                    error = table.Column<string>(type: "text", nullable: true),
                    latency_ms = table.Column<int>(type: "integer", nullable: false),
                    prompt_tokens = table.Column<int>(type: "integer", nullable: true),
                    completion_tokens = table.Column<int>(type: "integer", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_query_attempts", x => x.id);
                    table.CheckConstraint("ck_query_attempts_attempt_no", "attempt_no >= 1");
                    table.CheckConstraint("ck_query_attempts_stage", "stage IN ('extract', 'validate', 'execute')");
                    table.ForeignKey(
                        name: "fk_query_attempts_query_history_history_id",
                        column: x => x.history_id,
                        principalTable: "query_history",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "ix_query_attempts_history_id_attempt_no",
                table: "query_attempts",
                columns: new[] { "history_id", "attempt_no" },
                unique: true);

            migrationBuilder.CreateIndex(
                name: "ix_query_history_created_at",
                table: "query_history",
                column: "created_at");

            migrationBuilder.CreateIndex(
                name: "ix_query_history_rerun_of_id",
                table: "query_history",
                column: "rerun_of_id");

            migrationBuilder.CreateIndex(
                name: "ix_query_history_user_id_created_at",
                table: "query_history",
                columns: new[] { "user_id", "created_at" },
                descending: new[] { false, true });

            migrationBuilder.CreateIndex(
                name: "ix_users_email",
                table: "users",
                column: "email",
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "query_attempts");

            migrationBuilder.DropTable(
                name: "query_history");

            migrationBuilder.DropTable(
                name: "users");
        }
    }
}
